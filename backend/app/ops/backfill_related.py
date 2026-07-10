"""
Backfill ``related:`` frontmatter + slug-link conversion — ADR-0067 D2, P2-1, P2-2.

Non-destructive, deterministic, zero-LLM, idempotent backfill op that brings EXISTING wiki
pages up to the ADR-0067 D2 frontmatter conventions:

  P2-1 — ``related: list[str]`` (resolved slugs of outbound wikilinks, cap 8, resolvable-only).
         Set/replace the ``related:`` key at the D2 position: after ``tags``, before ``sources``
         if any.  If a page has zero resolvable outbound links, ``related:`` is left absent (no
         empty list).

  P2-2 — ``[[Title]]`` / ``[[Title|alias]]`` links in the body where Target is a resolvable
         human title are rewritten to ``[[slug|Title]]`` / ``[[slug|alias]]`` so the link
         target matches the page file stem and rendering is unchanged.  Links already in slug
         form (``[[slug]]``, ``[[slug|alias]]``) and unresolvable links are left untouched.
         Links inside fenced code blocks (``` or ~~~) are never touched.

``apply=False`` (default) — dry-run: scan every wiki page in scope, report counts + up to
    five sample transformations, write NOTHING.
``apply=True``  — perform file writes atomically, update ``pages.content_hash`` in Postgres,
    re-embed the body into Qdrant, re-derive K5 wikilink edges; one ``data_version`` bump +
    one ``reresolve_dangling_links`` pass for the whole batch (I1 — never per-page).

``total_cost_usd = 0.0`` always (no LLM calls).  Bounded by ``max_pages`` (I7).  Single-flight
(409 from the endpoint while running).  Never raises.

Reuse (read-only, strict-ownership boundary):
  ``ops/enrich_wikilinks.py``   — ``_split_frontmatter``, ``_rejoin``
  ``ingest/orchestrator.py``    — ``reindex_wiki_page_body``, ``bump_version``, ``_slugify``
  ``wiki/links.py``             — ``parse_wikilinks``, ``reresolve_dangling_links``
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import settings
from app.db import get_session
from app.models import Page

logger = logging.getLogger(__name__)

# ── Bounds (I7) ───────────────────────────────────────────────────────────────

DEFAULT_MAX_PAGES: int = 500
MAX_PAGES_HARD_CAP: int = 2_000

# Maximum outbound slugs emitted into ``related:`` (mirrors _resolve_related_slugs cap).
_RELATED_CAP: int = 8

# Maximum dry-run sample pages to include in BackfillSummary.samples.
_SAMPLE_CAP: int = 5

# Wikilink regex — same pattern as wiki/links.py _WIKILINK_RE.
_WIKILINK_RE = re.compile(r"\[\[([^\[\]]+?)\]\]")

# Fenced-code-block fence-line detector (``` or ~~~ at start of line).
_FENCE_RE = re.compile(r"^(?:```|~~~)", re.MULTILINE)


# ── Result DTOs ───────────────────────────────────────────────────────────────


@dataclass
class BackfillSample:
    """One page from the dry-run sample report (first _SAMPLE_CAP changed pages)."""

    page_title: str | None
    file_path: str
    links_would_convert: list[str]  # ["[[Title]]" → "[[slug|Title]]", ...]
    related_would_add: list[str]  # slugs that would become related:


@dataclass
class BackfillSummary:
    """Outcome of one backfill-related run."""

    pages_scanned: int = 0
    pages_changed: int = 0
    links_converted: int = 0
    related_added: int = 0  # number of pages that gained or changed related:
    skipped_no_fm: int = 0  # pages without frontmatter (skipped for related addition)
    total_cost_usd: float = 0.0  # always 0.0
    stopped_reason: str = "complete"  # complete | maxpages | error
    max_pages: int = 0
    apply: bool = False
    samples: list[BackfillSample] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "pages_scanned": self.pages_scanned,
            "pages_changed": self.pages_changed,
            "links_converted": self.links_converted,
            "related_added": self.related_added,
            "skipped_no_fm": self.skipped_no_fm,
            "total_cost_usd": 0.0,
            "stopped_reason": self.stopped_reason,
            "max_pages": self.max_pages,
            "apply": self.apply,
            "samples": [
                {
                    "page_title": s.page_title,
                    "file_path": s.file_path,
                    "links_would_convert": s.links_would_convert,
                    "related_would_add": s.related_would_add,
                }
                for s in self.samples
            ],
        }


# ── Module-level single-flight state ─────────────────────────────────────────


@dataclass
class _BackfillRelatedState:
    """Module-level single-flight guard (read by the endpoint to 409 / report)."""

    is_running: bool = False
    last_summary: BackfillSummary | None = None


_state = _BackfillRelatedState()


def is_running() -> bool:
    """True if a backfill-related run is currently in flight (single-flight guard)."""
    return _state.is_running


def get_last_summary() -> BackfillSummary | None:
    """Return the summary of the most recently COMPLETED run (None if never ran)."""
    return _state.last_summary


def clamp_bounds(max_pages: int | None) -> int:
    """
    Clamp *max_pages* to ``[1, MAX_PAGES_HARD_CAP]``.  ``None`` → settings default or
    ``DEFAULT_MAX_PAGES``.  No token_budget — this op has zero LLM cost (I7).
    """
    default = int(getattr(settings, "backfill_related_max_pages", DEFAULT_MAX_PAGES))
    mp = default if max_pages is None else int(max_pages)
    return max(1, min(mp, MAX_PAGES_HARD_CAP))


# ── Resolver (one bulk query, no N+1) ────────────────────────────────────────


@dataclass
class _Resolver:
    """
    Three lookup maps over live pages, built in ONE indexed query (I1 — no vault walk).

    ``by_title``  exact ``Page.title`` → file stem (slug)
    ``by_lower``  ``lower(title)`` → file stem (first-hit-wins)
    ``by_slug``   ``_slugify(title)`` → file stem (first-hit-wins)
    ``slug_set``  all live file stems (used to detect already-slug targets)
    """

    by_title: dict[str, str]
    by_lower: dict[str, str]
    by_slug: dict[str, str]
    slug_set: set[str]

    def resolve_as_title(self, target: str) -> str | None:
        """
        Return the resolved slug ONLY when *target* looks like a human title
        (exact ``by_title`` hit or case-insensitive ``by_lower`` hit).

        Returns ``None`` for targets that are already slugs or are unresolvable.
        The ``by_slug`` map is intentionally NOT consulted here so
        ``[[aws-cloud]]`` (already a slug) stays untouched (P2-2 invariant).
        """
        hit = self.by_title.get(target)
        if hit is not None:
            return hit
        return self.by_lower.get(target.lower())

    def resolve(self, target: str) -> str | None:
        """
        Resolve *target* using all three strategies (for outbound-slug collection, P2-1).
        Mirrors ``_resolve_target`` in ``wiki/links.py``.
        """
        hit = self.by_title.get(target)
        if hit is not None:
            return hit
        hit = self.by_lower.get(target.lower())
        if hit is not None:
            return hit
        from app.ingest.orchestrator import _slugify  # noqa: PLC0415

        return self.by_slug.get(_slugify(target))


async def _build_resolver(vault_id: str) -> _Resolver:
    """
    Build title → slug maps from all live pages in ONE indexed query (I1 — no vault walk).
    Scoped to *vault_id*; ``deleted_at`` pages excluded.
    """
    from sqlalchemy import select  # noqa: PLC0415

    from app.ingest.orchestrator import _slugify  # noqa: PLC0415

    async with get_session() as sess:
        rows = (
            await sess.execute(
                select(Page.title, Page.file_path).where(
                    Page.vault_id == vault_id,
                    Page.deleted_at.is_(None),
                    Page.title.is_not(None),
                    Page.file_path.is_not(None),
                )
            )
        ).all()

    by_title: dict[str, str] = {}
    by_lower: dict[str, str] = {}
    by_slug: dict[str, str] = {}
    slug_set: set[str] = set()

    for row in rows:
        title = row.title
        file_path = row.file_path
        if not title or not file_path:
            continue
        slug = Path(file_path).stem
        slug_set.add(slug)
        by_title.setdefault(title, slug)
        by_lower.setdefault(title.lower(), slug)
        by_slug.setdefault(_slugify(title), slug)

    return _Resolver(by_title=by_title, by_lower=by_lower, by_slug=by_slug, slug_set=slug_set)


# ── Candidate page loader ─────────────────────────────────────────────────────


async def _load_candidate_pages(vault_id: str, max_pages: int) -> tuple[list[Page], bool]:
    """
    Bounded indexed read of live wiki pages (I1 — no vault walk, no reserved types).

    Returns ``(pages, hit_sql_limit)``; ``hit_sql_limit`` is True when the SELECT returned
    ``max_pages`` rows (there may be more candidates beyond the cap).
    """
    from sqlalchemy import select  # noqa: PLC0415

    _RESERVED_TYPES: frozenset[str] = frozenset({"overview", "index", "log"})

    async with get_session() as sess:
        scalars = (
            (
                await sess.execute(
                    select(Page)
                    .where(
                        Page.vault_id == vault_id,
                        Page.deleted_at.is_(None),
                        Page.file_path.like("wiki/%"),
                        Page.page_type.notin_(_RESERVED_TYPES),
                    )
                    .order_by(Page.updated_at.desc())
                    .limit(max_pages + 1)  # +1 to detect SQL-limit hit
                )
            )
            .scalars()
            .all()
        )

    pages = list(scalars)
    hit_limit = len(pages) > max_pages
    return pages[:max_pages], hit_limit


# ── Body manipulation ─────────────────────────────────────────────────────────


def _code_fence_spans(body: str) -> list[tuple[int, int]]:
    """
    Return ``(start, end)`` char ranges of fenced code blocks
    (matched as opening-fence … closing-fence pairs).  Unmatched
    fences are silently ignored (defence-in-depth).
    """
    spans: list[tuple[int, int]] = []
    fences = list(_FENCE_RE.finditer(body))
    i = 0
    while i + 1 < len(fences):
        spans.append((fences[i].start(), fences[i + 1].end()))
        i += 2
    return spans


def _in_fence(idx: int, fence_spans: list[tuple[int, int]]) -> bool:
    """True when character position *idx* falls inside a fenced code block."""
    return any(s <= idx < e for s, e in fence_spans)


def _rewrite_title_links(
    body: str,
    resolver: _Resolver,
) -> tuple[str, int, list[str]]:
    """
    Rewrite title-form wikilinks to slug form in *body* (P2-2, body-only, I5).

    Rules:
      ``[[Title]]``        → ``[[slug|Title]]``   (display unchanged)
      ``[[Title|alias]]``  → ``[[slug|alias]]``   (display unchanged)
      ``[[slug]]``         → unchanged            (already slug form)
      ``[[slug|alias]]``   → unchanged            (already slug form)
      Unresolvable targets → unchanged

    Links inside fenced code blocks (``` or ~~~) are never touched.

    Returns ``(new_body, links_converted_count, conversion_samples)`` where
    *conversion_samples* contains at most 10 human-readable descriptions
    ``"[[Old]] → [[new|Old]]"`` for the dry-run report.
    """
    fence_spans = _code_fence_spans(body)
    parts: list[str] = []
    last_end = 0
    count = 0
    samples: list[str] = []

    for m in _WIKILINK_RE.finditer(body):
        start, end = m.start(), m.end()

        # Never touch links inside code fences
        if _in_fence(start, fence_spans):
            parts.append(body[last_end:end])
            last_end = end
            continue

        inner = m.group(1)

        # Parse [[Target]] or [[Target|alias]] (strip #section from target)
        if "|" in inner:
            raw_target, alias = inner.split("|", 1)
            alias = alias  # keep alias string as-is
        else:
            raw_target, alias = inner, None

        target = raw_target.split("#", 1)[0].strip()
        if not target:
            parts.append(body[last_end:end])
            last_end = end
            continue

        # Resolve: is Target a human title (not already a slug)?
        resolved_slug = resolver.resolve_as_title(target)

        if resolved_slug is not None:
            # Target is a resolvable human title → rewrite to slug form
            display = alias if alias is not None else target
            new_link = f"[[{resolved_slug}|{display}]]"
            parts.append(body[last_end:start])
            parts.append(new_link)
            last_end = end
            count += 1
            if len(samples) < 10:
                samples.append(f"{m.group(0)} → {new_link}")
        else:
            # Already slug, unresolvable, or unrecognised → leave alone
            parts.append(body[last_end:end])
            last_end = end

    parts.append(body[last_end:])
    return "".join(parts), count, samples


def _collect_outbound_slugs(
    body: str,
    exclude_slug: str,
    resolver: _Resolver,
) -> list[str]:
    """
    Collect resolved slugs of the page's outbound wikilinks (P2-1).

    Uses the ORIGINAL (pre-rewrite) body — the wikilink targets are semantically
    identical before and after P2-2 rewriting.  Deduped, capped at ``_RELATED_CAP``,
    self-links excluded, only resolvable slugs returned (never a ghost slug).
    """
    from app.wiki.links import parse_wikilinks  # noqa: PLC0415

    parsed = parse_wikilinks(body)
    seen: set[str] = set()
    out: list[str] = []

    for pl in parsed:
        target = pl.target.split("#", 1)[0].strip()
        slug = resolver.resolve(target)
        if not slug or slug == exclude_slug or slug in seen:
            continue
        seen.add(slug)
        out.append(slug)
        if len(out) >= _RELATED_CAP:
            break

    return out


# ── Frontmatter manipulation (line-based, byte-exact for all other keys) ──────


def _patch_frontmatter_related(fm_block: str, related_slugs: list[str]) -> str:
    """
    Set/replace the ``related:`` key in *fm_block* at the D2 position:
    after the ``tags`` block, before ``sources`` if any, else at end of inner block.

    All other lines are preserved byte-for-byte (no YAML round-trip).
    If *related_slugs* is empty, any existing ``related:`` key is removed and
    nothing is inserted (D2: never emit ``related: []``).

    *fm_block* must be the ``"---\\n...\\n---\\n"`` block returned by
    ``_split_frontmatter``.  Malformed input (no opening/closing ``---``) is
    returned unchanged.
    """
    lines = fm_block.splitlines(keepends=True)

    # ── Validate structure ────────────────────────────────────────────────────
    if not lines or lines[0].rstrip("\n").rstrip("\r") != "---":
        return fm_block  # malformed — leave alone

    close_idx = -1
    for i in range(1, len(lines)):
        if lines[i].rstrip("\n").rstrip("\r") == "---":
            close_idx = i
            break
    if close_idx == -1:
        return fm_block  # no closing fence

    inner = list(lines[1:close_idx])

    # ── Step 1: Remove any existing ``related:`` key + its continuation lines ─
    # A top-level YAML key: starts at column 0, not a comment (#), not a list
    # item (-), and contains a colon.
    def _is_top_key(line: str) -> bool:
        raw = line.rstrip("\n").rstrip("\r")
        return bool(
            raw
            and not raw.startswith(" ")
            and not raw.startswith("\t")
            and not raw.startswith("-")
            and not raw.startswith("#")
            and ":" in raw
        )

    cleaned: list[str] = []
    in_related = False
    for line in inner:
        if _is_top_key(line):
            key = line.split(":", 1)[0].strip()
            if key == "related":
                in_related = True
                continue  # drop this key line
            else:
                in_related = False
        elif in_related:
            # Continuation of the related block (list items / indented values) — drop
            continue

        if not in_related:
            cleaned.append(line)

    if not related_slugs:
        # Simply remove related: and return
        return "---\n" + "".join(cleaned) + "---\n"

    # ── Step 2: Build the related block ──────────────────────────────────────
    # Emit using the same unindented-list style that PyYAML / python-frontmatter uses.
    related_block: list[str] = ["related:\n"] + [f"- {slug}\n" for slug in related_slugs]

    # ── Step 3: Find the D2 insertion point ──────────────────────────────────
    # After the last line belonging to the ``tags`` block; before ``sources:`` if any;
    # else at the end of the inner block.
    tags_end_idx: int = -1  # index of the last line in the tags block
    sources_start_idx: int = -1  # index of the ``sources:`` line
    in_tags = False

    for i, line in enumerate(cleaned):
        if _is_top_key(line):
            key = line.split(":", 1)[0].strip()
            if key == "tags":
                in_tags = True
                tags_end_idx = i
            elif key == "sources":
                in_tags = False
                sources_start_idx = i
                break
            else:
                if in_tags:
                    in_tags = False
        elif in_tags:
            # Continuation of the tags block (list items / inline value)
            tags_end_idx = i

    if sources_start_idx != -1:
        insert_at = sources_start_idx
    elif tags_end_idx != -1:
        insert_at = tags_end_idx + 1
    else:
        insert_at = len(cleaned)

    result_inner = cleaned[:insert_at] + related_block + cleaned[insert_at:]
    return "---\n" + "".join(result_inner) + "---\n"


# ── Per-page I/O helper ───────────────────────────────────────────────────────


def _read_page_split(page: Page) -> tuple[str, str] | None:
    """
    Read the page file and split into ``(fm_block, body)`` (reusing
    ``enrich_wikilinks._split_frontmatter``).  Returns ``None`` on I/O error.
    """
    from app.ops.enrich_wikilinks import _split_frontmatter  # noqa: PLC0415

    abs_path = (settings.vault_root / page.file_path).resolve()
    try:
        text = abs_path.read_text(encoding="utf-8")
    except OSError:
        return None
    return _split_frontmatter(text)


# ── Main run ─────────────────────────────────────────────────────────────────


async def _run_inner(
    *,
    vault_id: str,
    apply: bool,
    max_pages: int,
    summary: BackfillSummary,
) -> None:
    """
    Inner scan loop — separated from :func:`run_backfill_related` so the outer function
    can wrap it in try/finally without duplicating the state-management block.

    For each candidate page:
      1. Read file; split frontmatter / body.
      2. P2-2: rewrite title-form body links.
      3. P2-1: collect outbound slugs from ORIGINAL body; patch frontmatter.
      4. If changed AND apply: write atomically + re-index incrementally (I1).
      5. Accumulate counts + dry-run samples.

    One ``data_version`` bump + one ``reresolve_dangling_links`` call for the whole
    batch after the loop (I1 — never per-page).
    """
    from app.ingest.orchestrator import bump_version, reindex_wiki_page_body  # noqa: PLC0415
    from app.ops.enrich_wikilinks import _rejoin  # noqa: PLC0415

    pages, hit_limit = await _load_candidate_pages(vault_id, max_pages)
    if hit_limit:
        summary.stopped_reason = "maxpages"

    # Build resolver ONCE from live pages (I1 — single indexed query for the whole run)
    resolver = await _build_resolver(vault_id)

    edited_any = False

    for page in pages:
        summary.pages_scanned += 1

        split = _read_page_split(page)
        if split is None:
            continue

        fm_block, body = split

        if not fm_block:
            # No frontmatter — cannot add related: safely; count and skip
            summary.skipped_no_fm += 1
            # Still attempt P2-2 body rewriting (links don't need frontmatter)
            new_body, links_converted, conversion_samples = _rewrite_title_links(body, resolver)
            if links_converted > 0:
                summary.pages_changed += 1
                summary.links_converted += links_converted
                if len(summary.samples) < _SAMPLE_CAP:
                    summary.samples.append(
                        BackfillSample(
                            page_title=page.title,
                            file_path=page.file_path,
                            links_would_convert=conversion_samples,
                            related_would_add=[],
                        )
                    )
                if apply:
                    new_file_text = new_body
                    await reindex_wiki_page_body(
                        page=page,
                        new_file_text=new_file_text,
                        body_for_embedding=new_body,
                        bump=False,
                    )
                    edited_any = True
            continue

        # P2-2: rewrite title-form links in body (BEFORE collecting outbound slugs —
        # conceptually separate, but we compute from ORIGINAL body for P2-1 below)
        new_body, links_converted, conversion_samples = _rewrite_title_links(body, resolver)

        # P2-1: collect outbound slugs from ORIGINAL body (semantic intent unchanged
        # by P2-2 formatting rewrite) → patch frontmatter
        exclude_slug = Path(page.file_path).stem
        new_related = _collect_outbound_slugs(body, exclude_slug, resolver)
        new_fm_block = _patch_frontmatter_related(fm_block, new_related)

        fm_changed = new_fm_block != fm_block
        body_changed = new_body != body

        if not fm_changed and not body_changed:
            continue

        # ── Accumulate counts ─────────────────────────────────────────────────
        summary.pages_changed += 1
        summary.links_converted += links_converted
        if fm_changed and new_related:
            summary.related_added += 1

        if len(summary.samples) < _SAMPLE_CAP:
            summary.samples.append(
                BackfillSample(
                    page_title=page.title,
                    file_path=page.file_path,
                    links_would_convert=conversion_samples,
                    related_would_add=new_related,
                )
            )

        # ── Apply (write back + incremental re-index) ─────────────────────────
        if apply:
            new_file_text = _rejoin(new_fm_block, new_body)
            await reindex_wiki_page_body(
                page=page,
                new_file_text=new_file_text,
                body_for_embedding=new_body,
                bump=False,  # one bump for the whole batch (I1)
            )
            edited_any = True

    # ── Single data_version bump + dangling-link re-resolve for the whole batch ──
    if apply and edited_any:
        await bump_version()
        try:
            from app.wiki.links import reresolve_dangling_links  # noqa: PLC0415

            async with get_session() as sess:
                reconnected = await reresolve_dangling_links(sess)
            logger.info(
                "backfill-related: reresolve_dangling_links reconnected %d links", reconnected
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("backfill-related: reresolve_dangling_links failed: %s", exc)


async def run_backfill_related(
    vault_id: str,
    *,
    apply: bool = False,
    max_pages: int | None = None,
) -> BackfillSummary:
    """
    Run ONE bounded ADR-0067 D2 backfill pass (P2-1 + P2-2).

    ``apply=False`` — dry-run: scan and report without writing.
    ``apply=True``  — perform file writes + incremental re-index + one ``data_version`` bump.

    Bounded by ``max_pages`` (I7).  ``total_cost_usd = 0.0`` always.  Single-flight — the
    caller (endpoint) must check :func:`is_running` and 409 before calling.  Never raises.
    """
    mp = clamp_bounds(max_pages)
    summary = BackfillSummary(max_pages=mp, apply=apply)

    _state.is_running = True
    try:
        await _run_inner(vault_id=vault_id, apply=apply, max_pages=mp, summary=summary)
    except Exception as exc:  # noqa: BLE001 — never raise into background task
        summary.stopped_reason = "error"
        logger.warning("backfill-related: run failed (vault=%s): %s", vault_id, exc)
    finally:
        _state.is_running = False
        _state.last_summary = summary

    logger.info(
        "backfill-related: pages_scanned=%d pages_changed=%d links_converted=%d "
        "related_added=%d cost_usd=0.0 apply=%s stopped_reason=%s vault=%s",
        summary.pages_scanned,
        summary.pages_changed,
        summary.links_converted,
        summary.related_added,
        apply,
        summary.stopped_reason,
        vault_id,
    )
    return summary


# ── CLI dry-run guard ─────────────────────────────────────────────────────────

if __name__ == "__main__":  # pragma: no cover
    import argparse
    import asyncio
    import json
    import sys

    _parser = argparse.ArgumentParser(
        description=(
            "ADR-0067 D2 backfill — related: frontmatter + slug-link conversion [P2-1,P2-2]. "
            "DRY-RUN by default (no file writes). Pass --apply to perform writes."
        )
    )
    _parser.add_argument("--apply", action="store_true", help="Perform actual writes")
    _parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        metavar="N",
        help=f"Max pages to scan (default {DEFAULT_MAX_PAGES}, hard cap {MAX_PAGES_HARD_CAP})",
    )
    _args = _parser.parse_args()

    if not _args.apply:
        print(
            "DRY-RUN mode — no writes will be performed. " "Pass --apply to commit changes.",
            file=sys.stderr,
        )

    async def _main() -> None:
        from app.config import settings as _settings  # noqa: PLC0415

        result = await run_backfill_related(
            _settings.vault_id,
            apply=_args.apply,
            max_pages=_args.max_pages,
        )
        print(json.dumps(result.as_dict(), indent=2, default=str))

    asyncio.run(_main())
