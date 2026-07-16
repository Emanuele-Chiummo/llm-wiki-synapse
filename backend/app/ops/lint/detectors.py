"""
Deterministic structural detectors (NO provider call — I1 / ADR-0037 §3.1).

  orphan-page     — a live wiki page with graph in-degree 0 (no resolved incoming wikilink).
  broken-wikilink — a dangling [[link]] in the links table (dangling=True). L1 / ADR-0037 B1.
  no-outlinks     — a live wiki page with zero outgoing wikilinks. L1 / ADR-0058 §L1.

All three read only the pages + links tables (I1 — no vault walk) and are bounded by their
own scan caps (ORPHAN_SCAN_MAX_PAGES / BROKEN_SCAN_MAX_LINKS / NO_OUTLINKS_SCAN_MAX_PAGES).

Also hosts the L3 fuzzy-suggestion helpers (port of lint.ts::suggestRelatedPage) shared by the
detectors above, plus the bounded reads that feed the semantic prompt (semantic.py).
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import unicodedata
import uuid
from typing import Any

from sqlalchemy import func, select

from app.db import get_session
from app.models import Page
from app.ops.lint._shared import (
    BROKEN_SCAN_MAX_LINKS,
    CANDIDATE_TITLES_MAX,
    NO_OUTLINKS_SCAN_MAX_PAGES,
    ORPHAN_SCAN_MAX_PAGES,
    FindingDTO,
)

logger = logging.getLogger(__name__)

# ── Fuzzy-suggestion constants (L3 — port of lint.ts suggestRelatedPage) ──────────
_RELATED_PAGE_SUGGESTION_MIN_SCORE: float = 0.08
_SAME_FOLDER_SCORE_BONUS: float = 0.08
_SINGLE_CJK_TOKEN_WEIGHT: float = 0.35
# Compiled pattern for tokenization: matches Unicode letters/digits, not underscore.
_WORD_RE: re.Pattern[str] = re.compile(r"[^\W_]+", re.UNICODE)
# CJK unified ideographs range (used in single-char expansion for CJK tokens).
_CJK_RE: re.Pattern[str] = re.compile(r"[㐀-鿿]")


# ── Deterministic structural checks (NO provider call — I1) ─────────────────────


async def _detect_orphans(vault_id: str) -> list[FindingDTO]:
    """
    Detect orphan pages: live wiki pages with graph in-degree 0 (ADR-0037 §3.1).

    in-degree 0 = no RESOLVED incoming wikilink (links.target_page_id == page.id,
    dangling=false) from a content wiki page. Reads only the pages + links tables
    (I1 — no vault walk). Bounded at ORPHAN_SCAN_MAX_PAGES.

    L-bug1 parity fix: inbound links are counted ONLY from content pages (source page
    must be a live wiki/* page whose basename is NOT index.md or log.md). Links from
    index.md/log.md do NOT count as inbound — they are navigation roots and linking
    nearly everything, which made almost nothing appear as an orphan under the old
    unfiltered query. overview.md is intentionally NOT excluded (L4 parity).

    index.md / log.md are excluded from the candidate set (they are navigation roots).
    overview.md is eligible (L4 parity with lint.ts:160-162 which only excludes index/log).

    L3: each orphan finding includes a `suggested_target` + `suggested_page_id` pointing
    to the page that *should* link to the orphan (token-overlap fuzzy scorer, port of
    lint.ts suggestRelatedPage, direction="source" — bounded to CANDIDATE_TITLES_MAX).

    L5: severity is `info` (matches the reference lint.ts orphan category).
    """
    out: list[FindingDTO] = []
    try:
        from app.models import Link

        async with get_session() as session:
            # Live wiki pages (exclude raw/* tracking rows and navigation roots).
            page_rows = list(
                (
                    await session.execute(
                        select(Page.id, Page.title, Page.file_path)
                        .where(
                            Page.vault_id == vault_id,
                            Page.deleted_at.is_(None),
                            Page.file_path.like("wiki/%"),
                        )
                        .order_by(Page.created_at.asc())
                        .limit(ORPHAN_SCAN_MAX_PAGES)
                    )
                ).all()
            )

            # Resolved incoming-link target ids (in-degree >= 1). Only count links whose
            # SOURCE page is a live wiki content page in THIS vault that is not index.md or
            # log.md (basename-based exclusion so subdirectory index/log variants are also
            # excluded). The vault join stops a cross-vault same-id link from masking an
            # orphan (from 1.3.12); the index/log exclusion stops index.md — which links
            # nearly everything — from masking true orphans (llm_wiki parity, L-bug1).
            target_rows = list(
                (
                    await session.execute(
                        select(func.distinct(Link.target_page_id))
                        .join(Page, Link.source_page_id == Page.id)
                        .where(
                            Page.vault_id == vault_id,
                            Page.deleted_at.is_(None),
                            Page.file_path.like("wiki/%"),
                            Page.file_path.not_like("%/index.md"),
                            Page.file_path.not_like("%/log.md"),
                            Link.target_page_id.isnot(None),
                        )
                    )
                ).scalars()
            )
            linked_ids = {str(t) for t in target_rows if t is not None}

            # L3: load candidate pages for fuzzy suggestion (bounded, I7).
            candidates = await _load_candidate_pages_fuzzy(vault_id, session)

        for pid, title, file_path in page_rows:
            rel = (file_path or "").lower()
            base = rel.rsplit("/", 1)[-1]
            # L4 parity: exclude only index.md and log.md (navigation roots).
            # overview.md is now eligible for orphan detection (lint.ts:160-162).
            if base in {"index.md", "log.md"}:
                continue
            if str(pid) in linked_ids:
                continue

            # L3: suggest a SOURCE page that should link to this orphan.
            suggested_target: str | None = None
            suggested_page_id: uuid.UUID | None = None
            proposed_action: str | None = None
            suggestion = _fuzzy_suggest_page(
                page_title=title or "",
                page_fp=file_path or "",
                candidates=candidates,
                exclude_page_fp=file_path or "",  # never suggest self
                exclude_titles=None,  # direction="source" — no outlink exclusion
            )
            if suggestion is not None:
                suggested_target, sugg_id_str = suggestion
                suggested_page_id = uuid.UUID(sugg_id_str)
                proposed_action = f"Add [[{title or base}]] to ## Related in {suggested_target!r}."

            out.append(
                FindingDTO(
                    category="orphan-page",
                    severity="info",  # L5 — info, matching reference
                    description=(
                        f"Page {title or rel!r} has no incoming wikilinks (orphan). "
                        "It is unreachable by graph navigation."
                    ),
                    target_title=title,
                    target_page_id=uuid.UUID(str(pid)),
                    proposed_action=proposed_action,
                    suggested_target=suggested_target,  # L3 — source page title
                    suggested_page_id=suggested_page_id,  # L3 — source page id
                )
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("_detect_orphans: failed (non-fatal): %s", exc)
    return out


# ── L1 — broken-wikilink detection (deterministic, NO provider call) ────────────


async def _detect_broken_wikilinks(vault_id: str) -> list[FindingDTO]:
    """
    Detect broken wikilinks: Link rows with dangling=True for the vault (L1 / ADR-0037 B1).

    For each dangling link:
      - category = "broken-wikilink", severity = "warning"
      - target_page_id = the REFERENCING page id (so the UI "Open" opens the page
        containing the broken link — inverted vs other categories per ADR review note)
      - target_title = the dangling target text (the [[broken]] part)
      - suggested_target / suggested_page_id: tolerant resolver result (L2)
      - proposed_action: "Rewrite [[old]] → [[Suggested]]" when suggestion found, else None

    DEDUP (within-scan):
      (a) one finding per (referencing_page_id, target_text) — enforced via seen set
      (b) skip if an OPEN finding with same category+target_page_id+target_title already in DB

    Bounded at BROKEN_SCAN_MAX_LINKS (I7). Reads links + pages tables only (I1).
    """
    out: list[FindingDTO] = []
    try:
        from app.models import Link

        async with get_session() as session:
            # Load dangling links for this vault via the source page's vault_id (I1).
            # Join to the referencing page so we can filter by vault_id and get the title.
            dangling_rows = list(
                (
                    await session.execute(
                        select(
                            Link.id,
                            Link.source_page_id,
                            Link.target_title,
                            Page.title.label("referencing_title"),
                        )
                        .join(Page, Link.source_page_id == Page.id)
                        .where(
                            Link.dangling.is_(True),
                            Page.vault_id == vault_id,
                            Page.deleted_at.is_(None),
                        )
                        .order_by(Link.created_at.asc())
                        .limit(BROKEN_SCAN_MAX_LINKS)
                    )
                ).all()
            )

            if not dangling_rows:
                return out

            # BE-PERF-1: build resolver maps ONCE for the WHOLE scan (one full-table SELECT
            # over live pages), not once per dangling link. The previous code called
            # resolve_suggested_target(target_text, session) INSIDE this loop, which rebuilt
            # _build_resolver_maps (a full-table page query) on every iteration — up to
            # BROKEN_SCAN_MAX_LINKS (1000) redundant SELECTs per scan.
            from app.wiki.links import _build_resolver_maps, resolve_suggested_target_with_maps

            maps = await _build_resolver_maps(session, vault_id)

            # within-scan dedup set: (source_page_id_str, target_text).
            # NOTE: the previous cross-run dedup (skip if an OPEN broken-wikilink already exists)
            # was a workaround for finding accumulation. The category-aware supersede (§4 in
            # run_lint_scan) now closes prior runs' open findings each scan, so cross-run dedup is
            # redundant AND harmful (it would skip re-emitting a finding that supersede then
            # closes → the finding vanishes). Within-scan dedup is all that's needed now.
            seen_within_scan: set[tuple[str, str]] = set()

            # Collect the distinct dangling target texts up front so the (pure CPU)
            # fuzzy-suggestion work for ALL of them can run in a SINGLE asyncio.to_thread call
            # (BE-PERF-1) instead of blocking the event loop once per link. Levenshtein scoring
            # against every live page title is O(links × pages) in the worst case — with
            # BROKEN_SCAN_MAX_LINKS=1000 links this can be ~2M synchronous string-distance ops,
            # which must never run inline on the event loop (would stall chat/status polling).
            unique_targets = sorted({row[2] for row in dangling_rows if row[2]})

            def _compute_suggestions(
                targets: list[str],
            ) -> dict[str, tuple[uuid.UUID, str] | None]:
                return {t: resolve_suggested_target_with_maps(t, maps) for t in targets}

            suggestions_by_target = await asyncio.to_thread(_compute_suggestions, unique_targets)

            for _link_id, source_page_id, target_text, referencing_title in dangling_rows:
                if not target_text:
                    continue
                src_str = str(source_page_id)
                dedup_key = (src_str, target_text)

                # (a) within-scan dedup
                if dedup_key in seen_within_scan:
                    continue
                seen_within_scan.add(dedup_key)

                ref_title = referencing_title or src_str
                description = (
                    f"Broken link: [[{target_text}]] — target page not found. " f"(in {ref_title})"
                )

                # L2: tolerant resolver for suggestion — pre-computed above (BE-PERF-1).
                suggestion = suggestions_by_target.get(target_text)
                suggested_target: str | None = None
                suggested_page_id: uuid.UUID | None = None
                proposed_action: str | None = None

                if suggestion is not None:
                    suggested_page_id, suggested_target = suggestion
                    proposed_action = f"Rewrite [[{target_text}]] → [[{suggested_target}]]"

                out.append(
                    FindingDTO(
                        category="broken-wikilink",
                        severity="warning",
                        description=description,
                        # target_page_id = referencing page (so "Open" opens it — ADR review note)
                        target_page_id=uuid.UUID(src_str),
                        target_title=target_text,  # the dangling [[Target]] text
                        proposed_action=proposed_action,
                        suggested_target=suggested_target,
                        suggested_page_id=suggested_page_id,
                    )
                )

    except Exception as exc:  # noqa: BLE001
        logger.warning("_detect_broken_wikilinks: failed (non-fatal): %s", exc)
    return out


# ── L1 — no-outlinks detection (deterministic, NO provider call) ─────────────────


async def _detect_no_outlinks(vault_id: str) -> list[FindingDTO]:
    """
    Detect pages with zero outgoing wikilinks: live wiki pages with NO links rows
    where source_page_id == page.id (L1 / ADR-0058 §L1, reference lint.ts:267-276).

    Reads only the pages + links tables (I1 — no vault walk). Bounded at
    NO_OUTLINKS_SCAN_MAX_PAGES. index.md / log.md / overview.md excluded (same
    exclusions as _detect_orphans).

    L3: each finding includes a `suggested_target` pointing to the best related page
    the no-outlinks page should link to (fuzzy token-overlap scorer, bounded to
    CANDIDATE_TITLES_MAX, direction="target").

    L5: severity is `info` (matches the reference lint.ts no-outlinks category).
    """
    out: list[FindingDTO] = []
    try:
        from sqlalchemy import exists as sa_exists
        from sqlalchemy import not_

        from app.models import Link

        async with get_session() as session:
            # Subquery: page ids that HAVE at least one outgoing link.
            has_outlink_sq = select(Link.source_page_id).where(Link.source_page_id == Page.id)

            # Live wiki pages with ZERO outgoing links (no row in links where source=page).
            page_rows = list(
                (
                    await session.execute(
                        select(Page.id, Page.title, Page.file_path)
                        .where(
                            Page.vault_id == vault_id,
                            Page.deleted_at.is_(None),
                            Page.file_path.like("wiki/%"),
                            not_(sa_exists(has_outlink_sq)),
                        )
                        .order_by(Page.created_at.asc())
                        .limit(NO_OUTLINKS_SCAN_MAX_PAGES)
                    )
                ).all()
            )

            if not page_rows:
                return out

            # L3: load candidate pages for fuzzy suggestion (bounded, I7).
            candidates = await _load_candidate_pages_fuzzy(vault_id, session)

        for pid, title, file_path in page_rows:
            rel = (file_path or "").lower()
            base = rel.rsplit("/", 1)[-1]
            # L4 parity: exclude only index.md and log.md (navigation roots).
            # overview.md is now eligible for no-outlinks detection (lint.ts:160-162).
            if base in {"index.md", "log.md"}:
                continue

            # L3: suggest a TARGET page to link to (direction="target").
            suggested_target: str | None = None
            suggested_page_id: uuid.UUID | None = None
            proposed_action: str | None = None
            suggestion = _fuzzy_suggest_page(
                page_title=title or "",
                page_fp=file_path or "",
                candidates=candidates,
                exclude_page_fp=file_path or "",  # never suggest self
                exclude_titles=None,  # page has no outlinks → nothing to exclude
            )
            if suggestion is not None:
                suggested_target, sugg_id_str = suggestion
                suggested_page_id = uuid.UUID(sugg_id_str)
                proposed_action = f"Add [[{suggested_target}]] to ## Related in {title or base!r}."

            out.append(
                FindingDTO(
                    category="no-outlinks",
                    severity="info",  # L5 — info, matching reference
                    description=(
                        f"Page {title or rel!r} has no [[wikilink]] references to other pages."
                    ),
                    target_title=title,
                    target_page_id=uuid.UUID(str(pid)),
                    proposed_action=proposed_action,
                    suggested_target=suggested_target,  # L3
                    suggested_page_id=suggested_page_id,  # L3
                )
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("_detect_no_outlinks: failed (non-fatal): %s", exc)
    return out


# ── L3 — Fuzzy suggestion helpers (port of lint.ts suggestRelatedPage) ───────────


def _tokenize_for_suggestion(text: str) -> frozenset[str]:
    """
    Tokenize *text* for fuzzy page-suggestion scoring (L3).

    Port of lint.ts::tokenizeForSuggestion. NFKC-normalises, lower-cases, extracts
    word-tokens (letters + digits, no underscores, len >= 2). For CJK tokens, also adds
    each individual character (single-char CJK weight applied at scoring time).
    Returns a frozenset so it is hashable and safe to cache.
    """
    tokens: set[str] = set()
    normalized = unicodedata.normalize("NFKC", text).lower()
    for m in _WORD_RE.finditer(normalized):
        token = m.group(0)
        if len(token) >= 2:
            tokens.add(token)
        if _CJK_RE.search(token):
            for char in token:
                tokens.add(char)
    return frozenset(tokens)


def _fuzzy_score(
    source_tokens: frozenset[str],
    candidate_tokens: frozenset[str],
    same_folder: bool,
) -> float:
    """
    Token-overlap relevance score between two pages (L3, port of lint.ts suggestRelatedPage).

    overlap / sqrt(|A| * |B|) + same-folder bonus. CJK single chars weighted lower.
    Returns 0.0 when there is no token overlap.
    """
    if not source_tokens or not candidate_tokens:
        return 0.0
    overlap: float = 0.0
    for token in source_tokens:
        if token in candidate_tokens:
            overlap += 1.0 if len(token) > 1 else _SINGLE_CJK_TOKEN_WEIGHT
    if overlap == 0.0:
        return 0.0
    score = overlap / math.sqrt(max(1, len(source_tokens)) * max(1, len(candidate_tokens)))
    if same_folder:
        score += _SAME_FOLDER_SCORE_BONUS
    return score


async def _load_candidate_pages_fuzzy(
    vault_id: str,
    session: Any,
) -> list[tuple[str, str, str]]:
    """
    Bounded load of (id_str, title, file_path) for all live wiki pages in the vault
    (L3 — fuzzy suggestion candidate pool; capped at CANDIDATE_TITLES_MAX, I7).

    Reads only the pages table (I1 — no vault walk). Ordered by updated_at DESC so
    the most recently edited pages lead the pool (better suggestions for active vaults).
    """
    rows = await session.execute(
        select(Page.id, Page.title, Page.file_path)
        .where(
            Page.vault_id == vault_id,
            Page.deleted_at.is_(None),
            Page.file_path.like("wiki/%"),
            Page.title.isnot(None),
        )
        .order_by(Page.updated_at.desc())
        .limit(CANDIDATE_TITLES_MAX)
    )
    return [(str(r[0]), r[1] or "", r[2] or "") for r in rows.all()]


def _fuzzy_suggest_page(
    *,
    page_title: str,
    page_fp: str,
    candidates: list[tuple[str, str, str]],
    exclude_page_fp: str,
    exclude_titles: set[str] | None = None,
) -> tuple[str, str] | None:
    """
    Return (best_title, best_id_str) for the candidate most relevant to *page_title*/*page_fp*
    using token-overlap scoring (L3, port of lint.ts::suggestRelatedPage).

    Args:
        page_title: title of the page being scored.
        page_fp: file_path of the page being scored (used for folder bonus + self-exclusion).
        candidates: list of (id_str, title, file_path) from _load_candidate_pages_fuzzy.
        exclude_page_fp: skip any candidate whose file_path equals this (avoids self-reference).
        exclude_titles: optional set of titles to skip (for direction="target": pages already
                        linked from the source page; for direction="source": not needed).

    Returns None when no candidate reaches _RELATED_PAGE_SUGGESTION_MIN_SCORE.
    """
    # Tokenize the source page using title + filename stem for richer overlap.
    path_stem = page_fp.rsplit("/", 1)[-1].replace(".md", "").replace("-", " ").replace("_", " ")
    source_text = f"{page_title}\n{path_stem}"
    source_tokens = _tokenize_for_suggestion(source_text)
    if not source_tokens:
        return None

    source_folder = page_fp.rsplit("/", 1)[0] if "/" in page_fp else ""
    exclude_norm: set[str] = {t.lower() for t in (exclude_titles or set())}

    best_id: str | None = None
    best_title: str | None = None
    best_score: float = 0.0

    for cand_id, cand_title, cand_fp in candidates:
        if cand_fp == exclude_page_fp:
            continue
        if cand_title.lower() in exclude_norm:
            continue

        cand_stem = (
            cand_fp.rsplit("/", 1)[-1].replace(".md", "").replace("-", " ").replace("_", " ")
        )
        cand_tokens = _tokenize_for_suggestion(f"{cand_title}\n{cand_stem}")
        cand_folder = cand_fp.rsplit("/", 1)[0] if "/" in cand_fp else ""
        score = _fuzzy_score(source_tokens, cand_tokens, same_folder=(cand_folder == source_folder))

        if score > best_score:
            best_score = score
            best_id = cand_id
            best_title = cand_title

    if best_score >= _RELATED_PAGE_SUGGESTION_MIN_SCORE and best_id and best_title:
        return (best_title, best_id)
    return None


# ── Bounded reads for the semantic prompt (I1) ──────────────────────────────────


async def _load_candidate_titles(vault_id: str) -> list[str]:
    """Bounded indexed read of live wiki page titles for the vault (I1 — no vault walk)."""
    async with get_session() as session:
        rows = await session.execute(
            select(Page.title)
            .where(
                Page.vault_id == vault_id,
                Page.deleted_at.is_(None),
                Page.title.isnot(None),
                Page.file_path.like("wiki/%"),
            )
            .order_by(Page.updated_at.desc())
            .limit(CANDIDATE_TITLES_MAX)
        )
        return [t for (t,) in rows.all() if t and t.strip()]


async def _load_page_digest(vault_id: str, *, max_pages: int = 60) -> str:
    """Compact title+type digest of live wiki pages for the semantic prompt (bounded — I1)."""
    async with get_session() as session:
        rows = await session.execute(
            select(Page.title, Page.page_type)
            .where(
                Page.vault_id == vault_id,
                Page.deleted_at.is_(None),
                Page.title.isnot(None),
                Page.file_path.like("wiki/%"),
            )
            .order_by(Page.updated_at.desc())
            .limit(max_pages)
        )
        lines: list[str] = []
        for title, ptype in rows.all():
            t = (title or "").strip() or "(untitled)"
            pt = (ptype or "?").strip()
            lines.append(f"- {t} [{pt}]")
    return "\n".join(lines) if lines else "(none)"
