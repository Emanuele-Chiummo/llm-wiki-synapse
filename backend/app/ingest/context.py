"""Ingest context assembly — moved from orchestrator.py (1.7.0 PR2).

Holds the provider vault/ingest context builders (F2/F3): purpose.md + schema.md +
the existing-pages catalogue, plus the R7-6 folderContext hint. Behaviour is
unchanged; cross-cutting/monkeypatched orchestrator symbols are reached via
``orch.<name>`` so ``app.ingest.orchestrator`` stays the single patch surface.
"""

from __future__ import annotations

import logging

import app.ingest.orchestrator as orch
from app.config import settings
from app.ingest.schemas import INDEX_TYPE, OVERVIEW_TYPE
from app.models import Page

logger = logging.getLogger(__name__)


def _load_vault_context() -> str:
    """
    Assemble the provider vault context (F2/F3): purpose.md + schema.md content. Used as the
    orchestrated analyze() context and as the CLI delegated system prompt. Missing files →
    empty section (tolerant).
    """
    parts: list[str] = []
    for name in ("purpose.md", "schema.md"):
        path = settings.vault_root / name
        try:
            # B3 fix: read without a prior exists() check to avoid TOCTOU — if the file
            # is removed between exists() and read_text() the OSError is silently skipped.
            text = path.read_text(encoding="utf-8")
            parts.append(f"# {name}\n{text}")
        except FileNotFoundError:
            pass  # file removed between check and read — tolerate silently
    return "\n\n".join(parts)


# ── F3 cross-ingest connectivity: existing-pages catalogue (K3) ──────────────────
#
# Each ingest otherwise produces an isolated graph island because the ingest LLM does not
# know which pages already exist, so it invents new titles → [[wikilinks]] don't match →
# links are dangling (no edge). nashsu/llm_wiki feeds the existing index catalogue to the
# LLM so it links to existing pages → one connected web. We inject the catalogue INTO THE
# CONTEXT STRING (never into provider code — I6).
#
# Bounded (I7): capped by title count AND char budget; a one-shot indexed query, no rescan.
_CATALOGUE_MAX_TITLES = 400
_CATALOGUE_MAX_CHARS = 8000
# Meta/infra page types the LLM should never link to as content pages.
_CATALOGUE_EXCLUDED_TYPES = frozenset({INDEX_TYPE, OVERVIEW_TYPE, "log"})


async def _load_existing_pages_catalogue() -> str:
    """
    Build the "Existing wiki pages — LINK TO THESE" catalogue (F3/K3 cross-ingest connectivity).

    Query live pages (deleted_at IS NULL), EXCLUDING meta/infra pages (index/log/overview page
    types AND anything under raw/sources/). Group the remaining real wiki-page titles by
    page_type and format a compact section instructing the LLM to link with the EXACT existing
    title instead of inventing a duplicate.

    Bounded (I7): capped at _CATALOGUE_MAX_TITLES titles and _CATALOGUE_MAX_CHARS chars. When the
    vault exceeds the cap we keep the most-recently-updated subset, append an explicit truncation
    note, and log.warning the count dropped (never silent). Returns "" when there is nothing to
    link to yet (first-ever ingest).
    """
    from sqlalchemy import select

    async with orch.get_session() as session:
        result = await session.execute(
            select(Page.title, Page.page_type).where(
                Page.deleted_at.is_(None),
                Page.title.is_not(None),
                Page.page_type.not_in(_CATALOGUE_EXCLUDED_TYPES),
                Page.file_path.not_like("raw/sources/%"),
            )
            # Most-recent first so truncation keeps the freshest pages (F3 intent).
            .order_by(Page.updated_at.desc())
        )
        rows = result.all()

    if not rows:
        return ""

    total = len(rows)
    truncated = total > _CATALOGUE_MAX_TITLES
    kept_rows = rows[:_CATALOGUE_MAX_TITLES]

    # Group titles by page_type, preserving the most-recent-first order within each group.
    grouped: dict[str, list[str]] = {}
    for title, page_type in kept_rows:
        grouped.setdefault(page_type or "other", []).append(title)

    header = (
        "# Existing wiki pages — LINK TO THESE\n"
        "When a concept/entity you write about already exists below, you MUST reference it with "
        "its EXACT title in a [[wikilink]] instead of creating a duplicate page. Only create a "
        "new page when nothing below fits."
    )
    sections: list[str] = [header]
    for page_type in sorted(grouped):
        titles = grouped[page_type]
        lines = "\n".join(f"- {t}" for t in titles)
        sections.append(f"## {page_type}\n{lines}")

    catalogue = "\n\n".join(sections)

    # Char-budget cap (I7): titles cap is the primary bound, but very long titles could still
    # blow the char budget — trim on a line boundary and note it.
    char_truncated = False
    if len(catalogue) > _CATALOGUE_MAX_CHARS:
        char_truncated = True
        cut = catalogue[:_CATALOGUE_MAX_CHARS]
        # Trim back to the last complete line so we never emit a half title.
        nl = cut.rfind("\n")
        catalogue = cut[:nl] if nl > 0 else cut

    if truncated or char_truncated:
        catalogue += (
            f"\n\n_(catalogue truncated: showing a subset of {total} existing pages, "
            "most recent first — link to any exact title you know exists.)_"
        )
        logger.warning(
            "_load_existing_pages_catalogue: vault has %d linkable pages; catalogue truncated "
            "to fit budget (max_titles=%d, max_chars=%d) — F3/I7",
            total,
            _CATALOGUE_MAX_TITLES,
            _CATALOGUE_MAX_CHARS,
        )

    return catalogue


async def _load_ingest_context() -> str:
    """
    Full ingest provider context (F2/F3): purpose.md + schema.md + the existing-pages catalogue.

    Assembled once per ingest in the async pipeline and threaded into BOTH the orchestrated loop
    and the delegated/CLI path so the LLM links to existing pages on every backend (I6 — the
    guidance lives in the context STRING, not in any provider). The catalogue is appended so it
    never shadows the schema/purpose rules.
    """
    base = _load_vault_context()
    try:
        catalogue = await _load_existing_pages_catalogue()
    except Exception as exc:  # noqa: BLE001
        # Best-effort enhancement — a DB hiccup must never fail ingest (I7). Degrade to
        # purpose+schema only; the LLM simply won't get the existing-pages hint this run.
        logger.warning(
            "_load_ingest_context: existing-pages catalogue unavailable (%s) — "
            "ingesting without it (F3 degrade)",
            exc,
        )
        catalogue = ""
    if not catalogue:
        return base
    return f"{base}\n\n{catalogue}" if base else catalogue


# ── R7-6: folderContext hint (F3 topical context from subfolder layout) ──────────
#
# When a source lives in subfolders under the import root (e.g. raw/sources/servicenow/itam/
# sam/foo.md), the relative folder path is a strong topical hint the LLM should use when
# classifying + writing pages. We derive a compact "servicenow / itam / sam" string from the
# origin_source relative path and inject it INTO THE CONTEXT STRING (never provider code — I6),
# so it reaches BOTH the orchestrated analyze() and the delegated/CLI system prompt.
#
# Bounded (I7): capped at _FOLDER_CONTEXT_MAX_SEGMENTS segments and _FOLDER_CONTEXT_MAX_CHARS.
_FOLDER_CONTEXT_MAX_SEGMENTS = 8
_FOLDER_CONTEXT_MAX_CHARS = 500
# Leading path prefixes stripped before computing the topical segments (the "import root").
_FOLDER_CONTEXT_ROOTS = ("raw/sources/", "raw/", "wiki/")


def _folder_context(origin_source: str) -> str:
    """
    Derive a compact folderContext hint from *origin_source* (R7-6), or "" when the file sits
    directly under a known root (no subfolders → no hint).

    "raw/sources/servicenow/itam/sam/foo.md" → "servicenow / itam / sam".
    Bounded to _FOLDER_CONTEXT_MAX_SEGMENTS segments and _FOLDER_CONTEXT_MAX_CHARS chars (I7).
    """
    if not origin_source:
        return ""
    # Normalize separators (F15 path normalization) and drop the filename.
    rel = origin_source.replace("\\", "/").lstrip("/")
    for root in _FOLDER_CONTEXT_ROOTS:
        if rel.startswith(root):
            rel = rel[len(root) :]
            break
    parts = [p for p in rel.split("/") if p]
    # Drop the trailing filename segment; only the directory path is topical context.
    segments = parts[:-1]
    if not segments:
        return ""
    segments = segments[:_FOLDER_CONTEXT_MAX_SEGMENTS]
    joined = " / ".join(segments)
    if len(joined) > _FOLDER_CONTEXT_MAX_CHARS:
        joined = joined[:_FOLDER_CONTEXT_MAX_CHARS].rstrip()
    return joined


def _folder_context_block(origin_source: str) -> str:
    """
    Build the folderContext section appended to the ingest context (R7-6), or "" when there is
    no subfolder hint. Phrased as an explicit topical hint for the analysis/classification step.
    """
    fc = _folder_context(origin_source)
    if not fc:
        return ""
    return (
        "# folderContext\n"
        f"This document comes from the folder path: {fc} — use it as topical context when "
        "classifying the document and naming/linking pages."
    )
