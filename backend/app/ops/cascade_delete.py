"""
F13 Cascade deletion of wiki pages (ADR-0026, AC-F13-1..7).

Single-pass, inference-free, loop-free operation.  Entry points:

  plan_cascade_delete(page_id)  → CascadePlan   (DRY-RUN: read-only, mutates nothing)
  cascade_delete(page_id)       → CascadeResult  (APPLY: single pass)

Critical invariants enforced here:
  I1  — NEVER a full vault rescan; references found via links back-reference index;
        (c) full-text fallback is bounded (CASCADE_FULLTEXT_MAX_FILES) and SKIPPED on
        the happy path (links-table hit).
  I2  — data_version bumped EXACTLY ONCE at the end; NO synchronous FA2 / GraphEngine call.
  I5  — dead-wikilink rewrites go through python-frontmatter loads/dumps round-trip;
        body regex is anchored and cannot touch the frontmatter block.
  I7  — single pass, no loop, no inference provider calls.

Do-NOT list (ADR-0026 §8):
  1. Never glob/walk vault/wiki/ to FIND references — use links table (I1).
  2. Never call GraphEngine.recompute() / FA2 inline (I2).
  3. Never bump data_version more than once (AC-F13-4c).
  4. Never leave a dead [[wikilink]] (I5).
  5. Never corrupt YAML frontmatter — always use frontmatter.loads/dumps (I5).
  6. Never delete a shared page whose sources[] retains another entry.
  7. Never skip preview — plan_cascade_delete is the mandatory dry-run seam.
  8. Never loop or call a provider (I7/I6).
  9. Never leave the raw/sources/ file on disk (AQ-v0.5-5).
  10. Never hard-delete the pages row — soft-delete (deleted_at) only.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import frontmatter  # python-frontmatter

from app.config import settings
from app.db import get_session
from app.models import Edge, Link, Page, VaultState
from app.qdrant_client import delete_point

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────

# Maximum live wiki pages to scan in the (c) full-text fallback (I7/ADR-0026 §3.1).
# Env override: CASCADE_FULLTEXT_MAX_FILES
CASCADE_FULLTEXT_MAX_FILES: int = int(os.environ.get("CASCADE_FULLTEXT_MAX_FILES", "5000"))

# Dead-link neutralisation style — only "plain" is M5 shipped behaviour (ADR-0026 §4.3).
CASCADE_DEAD_LINK_STYLE: str = os.environ.get("CASCADE_DEAD_LINK_STYLE", "plain")


# ── Wikilink regex (same grammar as app.wiki.links._WIKILINK_RE) ───────────────

_WIKILINK_RE = re.compile(r"\[\[([^\[\]]+?)\]\]")

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(title: str) -> str:
    """Filesystem-safe, lowercase slug (same as orchestrator._slugify)."""
    slug = _SLUG_RE.sub("-", title.strip().lower()).strip("-")
    return slug or "untitled"


# ── DTOs (ADR-0026 §2) ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WikilinkRewrite:
    """One dead [[Target]] → plain-text rewrite in one referencing wiki file."""

    source_page_id: uuid.UUID  # the wiki page (file) that contains the dead link
    file_path: str  # wiki/<subdir>/<slug>.md  (targeted write target)
    target_title: str  # the [[Target]] string being neutralised
    occurrences: int  # how many [[Target]] spans in this file's BODY


@dataclass(frozen=True)
class CascadePlan:
    """The computed effect of deleting page_id — returned by preview, consumed by apply."""

    target_page_id: uuid.UUID
    target_title: str | None
    target_file_path: str  # wiki/... of the page being deleted
    will_delete: list[uuid.UUID]  # pages whose sources[] becomes empty (incl. target)
    will_preserve_with_pruned_source: list[uuid.UUID]  # shared pages: keep, prune sources[]
    wikilinks_to_rewrite: list[WikilinkRewrite]  # dead-link cleanup edits (no-rescan)
    index_entry_will_be_removed: bool
    raw_source_to_delete: str | None  # raw/sources/... file to delete (AQ-v0.5-5); None if N/A
    shared_entity_warnings: list[str]  # source-overlap pages (edges) — WARN, never block
    match_methods_used: dict[str, str]  # file_path → "exact" | "slug" | "fulltext" (AC-F13-2)


@dataclass
class CascadeResult:
    """The applied outcome — backs the DELETE /pages/{id} response (AC-F13-5)."""

    deleted_page_id: uuid.UUID
    wikilinks_cleaned: int  # total [[Target]] spans neutralised
    index_entry_removed: bool
    shared_entity_warnings: list[str]
    files_written: int  # <= len(plan.wikilinks_to_rewrite) (AC-F13-4a; FU-P4-4)
    # Upper bound, not strict equality: the plan lists every page whose BODY had
    # >0 occurrences at plan time, but a rewrite is skipped (no increment) when the
    # body is unchanged at apply time (TOCTOU / occurrence counted only outside the
    # body) or a write fails. Equals len(...) on the happy path; fewer otherwise.
    data_version_after: int


class PageNotFoundError(Exception):
    """Raised when the target page does not exist or is already soft-deleted."""


# ── Internal helpers ───────────────────────────────────────────────────────────


def _count_body_occurrences(body: str, title: str) -> int:
    """
    Count how many [[Title]] wikilink spans reference *title* in *body*.

    Matches:
      [[Title]]            exact
      [[Title|alias]]      aliased
      [[Title#section]]    sectioned
      Case-insensitive via slug comparison so [[my page]] == "My Page".

    Returns the count of matching spans (de-duplicated per ADR-0026 §4.3 note:
    we count spans here for the occurrences field; the actual regex replaces all).
    """
    title_slug = _slugify(title)
    count = 0
    for m in _WIKILINK_RE.finditer(body):
        inner = m.group(1)
        # Strip alias
        target_part = inner.split("|")[0]
        # Strip section
        target_part = target_part.split("#")[0].strip()
        if target_part == title or _slugify(target_part) == title_slug:
            count += 1
    return count


def _rewrite_body(body: str, title: str) -> str:
    """
    Replace every [[Title]] dead-link in *body* with its display text (plain style).

    Rules (ADR-0026 §4.3):
      [[T]]         → T
      [[T|alias]]   → alias
      [[T#section]] → T
      case/slug variants matched by slugify comparison

    The replacement is performed with an anchored regex over the body string
    (BODY ONLY — frontmatter is never passed here; I5).
    """
    title_slug = _slugify(title)

    def _replace(m: re.Match[str]) -> str:
        inner = m.group(1)
        # Parse alias
        if "|" in inner:
            target_part, alias_part = inner.split("|", 1)
        else:
            target_part = inner
            alias_part = None

        # Parse section from target
        target_clean = target_part.split("#")[0].strip()

        # Check match (exact or slug)
        if target_clean == title or _slugify(target_clean) == title_slug:
            return alias_part.strip() if alias_part else target_clean
        return m.group(0)  # no match — leave unchanged

    return _WIKILINK_RE.sub(_replace, body)


def _rewrite_body_preserving_frontmatter(raw: str, title: str) -> str | None:
    """
    Rewrite dead [[wikilinks]] to *title* in the BODY only, preserving the YAML frontmatter
    block BYTE-FOR-BYTE (I5, ADR-0026 §4.3 / DEFECT-F13-002).

    A `frontmatter.loads`/`dumps` round-trip reorders keys and renormalises list indentation
    (PyYAML default Dumper), so for a body-only edit we split on the `---` fences and leave the
    frontmatter text untouched. Returns the new file text, or None if the body is unchanged.
    Files without a leading `---` fence are rewritten whole.
    """
    if raw.startswith("---\n"):
        parts = raw.split("---\n", maxsplit=2)
        if len(parts) == 3:  # ['', <frontmatter block>, <body>]
            fm_block, body = parts[1], parts[2]
            new_body = _rewrite_body(body, title)
            if new_body == body:
                return None
            return "---\n" + fm_block + "---\n" + new_body
    new_raw = _rewrite_body(raw, title)
    return None if new_raw == raw else new_raw


def _load_page_file(file_path: str) -> tuple[str, str]:
    """
    Load a wiki file and split into (frontmatter_text, body).

    Uses python-frontmatter to parse; returns the raw serialised frontmatter text
    (via frontmatter.dumps on an otherwise-unmodified Post) and the body string.
    Returns ("", full_content) if parsing fails.
    """
    abs_path = settings.vault_root / file_path
    try:
        raw = abs_path.read_text(encoding="utf-8")
        post = frontmatter.loads(raw)
        # Re-serialise the full document to get byte-identical frontmatter
        return raw, post.content
    except Exception as exc:  # noqa: BLE001
        logger.warning("cascade_delete: failed to load %s: %s", file_path, exc)
        return "", ""


# ── 3-method reference matching (ADR-0026 §3) ─────────────────────────────────


async def _method_a_exact(
    page_id: uuid.UUID,
    title: str | None,
) -> dict[uuid.UUID, tuple[str, str]]:
    """
    Method (a): exact resolved/title match in the links table (ADR-0026 §3 — primary).

    Finds rows where target_page_id == page_id OR target_title == title.
    Returns {source_page_id: (file_path, target_title)}.
    """
    from sqlalchemy import or_, select

    results: dict[uuid.UUID, tuple[str, str]] = {}

    async with get_session() as session:
        stmt = select(Link.source_page_id, Link.target_title).where(Link.target_page_id == page_id)
        if title is not None:
            stmt = select(Link.source_page_id, Link.target_title).where(
                or_(Link.target_page_id == page_id, Link.target_title == title)
            )
        rows = await session.execute(stmt)
        link_rows = list(rows.all())

        if not link_rows:
            return results

        # Resolve file_path for each source_page_id (must be a live wiki page)
        source_ids = list({row[0] for row in link_rows})
        page_rows = await session.execute(
            select(Page.id, Page.file_path).where(
                Page.id.in_(source_ids),
                Page.deleted_at.is_(None),
            )
        )
        id_to_path: dict[uuid.UUID, str] = {r[0]: r[1] for r in page_rows.all()}

        for source_id, target_title in link_rows:
            if source_id in id_to_path:
                results[source_id] = (id_to_path[source_id], target_title)

    return results


async def _method_b_slug(
    already_found: set[uuid.UUID],
    title: str,
) -> dict[uuid.UUID, tuple[str, str]]:
    """
    Method (b): slug-normalised match in the links table (ADR-0026 §3 — secondary).

    Reads remaining Link rows not covered by (a), compares slugify(target_title) == slugify(T).
    Still NO filesystem walk — reads links rows only.
    Returns {source_page_id: (file_path, target_title)}.
    """
    from sqlalchemy import select

    title_slug = _slugify(title)
    results: dict[uuid.UUID, tuple[str, str]] = {}

    async with get_session() as session:
        # Read all link rows whose source_page_id is not already found
        all_rows = await session.execute(
            select(Link.source_page_id, Link.target_title).where(
                Link.source_page_id.not_in(list(already_found)) if already_found else True  # type: ignore[arg-type]
            )
        )
        candidate_rows = [
            (row[0], row[1]) for row in all_rows.all() if _slugify(row[1]) == title_slug
        ]

        if not candidate_rows:
            return results

        source_ids = list({r[0] for r in candidate_rows})
        page_rows = await session.execute(
            select(Page.id, Page.file_path).where(
                Page.id.in_(source_ids),
                Page.deleted_at.is_(None),
            )
        )
        id_to_path: dict[uuid.UUID, str] = {r[0]: r[1] for r in page_rows.all()}

        for source_id, target_title in candidate_rows:
            if source_id in id_to_path and source_id not in already_found:
                results[source_id] = (id_to_path[source_id], target_title)

    return results


async def _method_c_fulltext(
    already_found: set[uuid.UUID],
    title: str,
) -> dict[str, tuple[str | None, str]]:
    """
    Method (c): bounded full-text scan fallback (ADR-0026 §3.1 — last resort only).

    Scans ONLY the enumerated live wiki pages from the pages table (NOT a vault walk).
    Limited to CASCADE_FULLTEXT_MAX_FILES (I7).
    SKIPPED when already_found covers all candidates — caller is responsible for this gate.

    Returns {file_path: (source_page_id_or_None, target_title_as_written)}.
    NOTE: source_page_id may be None for files not in the pages table (hand-edited files).
    """
    from sqlalchemy import select

    results: dict[str, tuple[str | None, str]] = {}

    async with get_session() as session:
        # Enumerate live wiki pages only (NOT a vault walk — I1)
        wiki_rows = await session.execute(
            select(Page.id, Page.file_path)
            .where(
                Page.deleted_at.is_(None),
                Page.file_path.like("wiki/%"),
                Page.id.not_in(list(already_found)) if already_found else True,  # type: ignore[arg-type]
            )
            .limit(CASCADE_FULLTEXT_MAX_FILES)
        )
        candidates = list(wiki_rows.all())

    # Substring scan — read each file ONCE (not an index rebuild)
    for page_id_val, file_path in candidates:
        abs_path = settings.vault_root / file_path
        try:
            raw = abs_path.read_text(encoding="utf-8")
        except OSError:
            continue

        post = frontmatter.loads(raw)
        body = post.content
        count = _count_body_occurrences(body, title)
        if count > 0:
            results[file_path] = (str(page_id_val) if page_id_val else None, title)
            logger.info(
                "cascade_delete method(c): fulltext match file_path=%s target=%r occurrences=%d",
                file_path,
                title,
                count,
            )

    return results


# ── Preserve-shared partition (ADR-0026 §4.1) ──────────────────────────────────


async def _partition_shared_wiki_pages(
    target_file_path: str,
) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    """
    Compute will_delete / will_preserve_with_pruned_source for wiki pages that reference
    target_file_path in their sources[] JSONB (ADR-0026 §4.1 preserve-shared rule).

    - DELETE a wiki page iff removing target_file_path from its sources[] leaves sources[] empty.
    - PRESERVE + PRUNE if at least one other source entry remains after removal.

    Returns (will_delete, will_preserve).
    """
    from sqlalchemy import select

    will_delete: list[uuid.UUID] = []
    will_preserve: list[uuid.UUID] = []

    # Find wiki pages whose sources[] contains target_file_path
    # JSONB contains-element (@>) on Postgres; Python fallback for SQLite tests
    async with get_session() as session:
        rows = await session.execute(
            select(Page.id, Page.sources).where(
                Page.deleted_at.is_(None),
                Page.file_path.like("wiki/%"),
            )
        )
        for page_id_val, sources in rows.all():
            if sources is None:
                continue
            if not isinstance(sources, list):
                continue
            if target_file_path not in sources:
                continue
            remaining = [s for s in sources if s != target_file_path]
            if not remaining:
                will_delete.append(uuid.UUID(str(page_id_val)))
            else:
                will_preserve.append(uuid.UUID(str(page_id_val)))

    return will_delete, will_preserve


async def _get_shared_entity_warnings(page_id: uuid.UUID) -> list[str]:
    """
    Find pages with kind='source' edges to page_id (source-overlap, ADR-0026 §4.1).

    Returns advisory warning strings — shared entities NEVER block the delete.
    """
    from sqlalchemy import or_, select

    warnings: list[str] = []

    async with get_session() as session:
        rows = await session.execute(
            select(Edge.source_page_id, Edge.target_page_id).where(
                or_(
                    Edge.source_page_id == page_id,
                    Edge.target_page_id == page_id,
                ),
                Edge.kind == "source",
            )
        )
        for src_id, tgt_id in rows.all():
            other_id = tgt_id if src_id == page_id else src_id
            # Resolve the title of the other page
            p_row = await session.execute(
                select(Page.title, Page.file_path).where(
                    Page.id == other_id,
                    Page.deleted_at.is_(None),
                )
            )
            p = p_row.first()
            if p is not None:
                label = p[0] or p[1]
                warnings.append(f"Page '{label}' shares source overlap with the deleted page")

    return warnings


# ── Build WikilinkRewrite list ─────────────────────────────────────────────────


async def _build_wikilink_rewrites(
    page_id: uuid.UUID,
    title: str | None,
    will_delete_ids: list[uuid.UUID],
) -> tuple[list[WikilinkRewrite], dict[str, str]]:
    """
    Run the 3-method union to find all files referencing the deleted page and build
    WikilinkRewrite records.  Returns (rewrites, match_methods_used).

    will_delete_ids includes the target page itself; we skip files whose page_id is in
    will_delete (they are being deleted anyway — no need to rewrite them).
    """
    if title is None:
        return [], {}

    delete_set = {str(i) for i in will_delete_ids}
    rewrites: list[WikilinkRewrite] = []
    match_methods_used: dict[str, str] = {}

    # Method (a) — exact match in links table
    a_results = await _method_a_exact(page_id, title)
    for source_id, (fp, tt) in a_results.items():
        if str(source_id) in delete_set:
            continue  # this file is being deleted; no need to rewrite
        logger.info(
            "cascade_delete method(a) exact: source_page_id=%s file_path=%s target=%r",
            source_id,
            fp,
            tt,
        )
        match_methods_used[fp] = "exact"
        _, body = _load_page_file(fp)
        count = _count_body_occurrences(body, title)
        if count > 0:
            rewrites.append(
                WikilinkRewrite(
                    source_page_id=source_id,
                    file_path=fp,
                    target_title=tt,
                    occurrences=count,
                )
            )

    # Method (b) — slug-normalised match
    already_found = set(a_results.keys())
    b_results = await _method_b_slug(already_found, title)
    for source_id, (fp, tt) in b_results.items():
        if str(source_id) in delete_set:
            continue
        logger.info(
            "cascade_delete method(b) slug: source_page_id=%s file_path=%s target=%r",
            source_id,
            fp,
            tt,
        )
        match_methods_used[fp] = "slug"
        _, body = _load_page_file(fp)
        count = _count_body_occurrences(body, title)
        if count > 0:
            rewrites.append(
                WikilinkRewrite(
                    source_page_id=source_id,
                    file_path=fp,
                    target_title=tt,
                    occurrences=count,
                )
            )

    # Method (c) — bounded full-text fallback. The no-rescan HAPPY PATH skips (c) entirely
    # when the links index (a)∪(b) already returned references (ADR-0026 §3.1, DEFECT-F13-001):
    # in an in-sync vault persist_links keeps that table current, so (c) — which opens every
    # live wiki file — must NOT run. (c) is the last resort only when the index found nothing
    # (e.g. a hand-edited file absent from links).
    all_found_ids = set(a_results.keys()) | set(b_results.keys())
    if not all_found_ids:
        c_results = await _method_c_fulltext(all_found_ids, title)
        for fp, (source_id_str, tt) in c_results.items():
            if fp in match_methods_used:
                continue  # already found by (a) or (b)
            logger.info(
                "cascade_delete method(c) fulltext: file_path=%s target=%r",
                fp,
                tt,
            )
            match_methods_used[fp] = "fulltext"
            _, body = _load_page_file(fp)
            count = _count_body_occurrences(body, title)
            if count > 0:
                source_id = uuid.UUID(source_id_str) if source_id_str else uuid.uuid4()
                rewrites.append(
                    WikilinkRewrite(
                        source_page_id=source_id,
                        file_path=fp,
                        target_title=tt,
                        occurrences=count,
                    )
                )

    return rewrites, match_methods_used


# ── Public API ─────────────────────────────────────────────────────────────────


async def plan_cascade_delete(page_id: uuid.UUID) -> CascadePlan:
    """
    DRY-RUN.  Compute the full effect of deleting page_id WITHOUT mutating any store or file.

    Read-only: no soft-delete, no Qdrant delete, no file write, no data_version bump.
    Raises PageNotFoundError if the page does not exist or is already soft-deleted
    (→ HTTP 404 from the preview endpoint).

    AC-F13 preview gate: the caller MUST call this before cascade_delete.
    """
    from sqlalchemy import select

    # ── Load the target page (must be live) ───────────────────────────────────
    async with get_session() as session:
        row = await session.execute(
            select(Page).where(
                Page.id == page_id,
                Page.deleted_at.is_(None),
            )
        )
        target = row.scalar_one_or_none()

    if target is None:
        raise PageNotFoundError(f"Page {page_id} not found or already deleted")

    target_title: str | None = target.title
    target_file_path: str = target.file_path

    # ── Preserve-shared partition (ADR-0026 §4.1) ─────────────────────────────
    # Relevant only when target is a raw/sources/ page (i.e. a source document)
    # and there are wiki pages referencing it in their sources[].
    will_delete: list[uuid.UUID] = [page_id]
    will_preserve: list[uuid.UUID] = []
    raw_source_to_delete: str | None = None

    if target_file_path.startswith("raw/sources/"):
        # The target IS a source document — partition wiki pages by their sources[]
        wiki_will_delete, wiki_will_preserve = await _partition_shared_wiki_pages(target_file_path)
        will_delete.extend(wiki_will_delete)
        will_preserve.extend(wiki_will_preserve)
        raw_source_to_delete = target_file_path

    # ── Shared-entity warnings (source-overlap edges — advisory only) ─────────
    shared_entity_warnings = await _get_shared_entity_warnings(page_id)

    # ── Build wikilink rewrites via 3-method matching ─────────────────────────
    wikilinks_to_rewrite, match_methods_used = await _build_wikilink_rewrites(
        page_id,
        target_title,
        will_delete,
    )

    # index.md will always have the entry removed (update_index re-reads live pages)
    index_entry_will_be_removed = target_file_path.startswith("wiki/")

    return CascadePlan(
        target_page_id=page_id,
        target_title=target_title,
        target_file_path=target_file_path,
        will_delete=will_delete,
        will_preserve_with_pruned_source=will_preserve,
        wikilinks_to_rewrite=wikilinks_to_rewrite,
        index_entry_will_be_removed=index_entry_will_be_removed,
        raw_source_to_delete=raw_source_to_delete,
        shared_entity_warnings=shared_entity_warnings,
        match_methods_used=match_methods_used,
    )


async def cascade_delete(page_id: uuid.UUID) -> CascadeResult:
    """
    SINGLE PASS (not a loop).  Compute the plan then apply it.

    Steps (ADR-0026 §5, in dependency order):
      1. plan_cascade_delete — compute everything read-only
      2. Soft-delete pages in will_delete (deleted_at = now())
      3. Hard-delete Qdrant points for will_delete
      4. Apply wikilink rewrites (frontmatter-safe; body only; I5)
      5. Prune sources[] for will_preserve_with_pruned_source
      6. Set dangling=True on residual link rows → deleted pages
      7. Delete edge rows touching deleted pages
      8. Call update_index() ONCE (deleted page auto-removed from live query)
      9. Delete raw/sources/ file from disk (AQ-v0.5-5)
     10. bump_version() EXACTLY ONCE; notify GraphCache (I2)

    Raises PageNotFoundError → HTTP 404 on already-soft-deleted id (AC-F13-5c/7c).
    Makes ZERO inference calls; ZERO FA2 calls (ADR-0026 §8 #2/#8).
    """
    from sqlalchemy import delete as sa_delete
    from sqlalchemy import or_, update

    # ── Step 1: Plan ──────────────────────────────────────────────────────────
    plan = await plan_cascade_delete(page_id)  # raises PageNotFoundError if missing/deleted

    # Emit shared-entity warnings
    for w in plan.shared_entity_warnings:
        logger.warning("cascade_delete: SHARED ENTITY — %s", w)

    now = datetime.now(UTC)

    # ── Step 2: Soft-delete will_delete pages ─────────────────────────────────
    async with get_session() as session:
        await session.execute(
            update(Page).where(Page.id.in_(plan.will_delete)).values(deleted_at=now, updated_at=now)
        )

    logger.info(
        "cascade_delete: soft-deleted %d page(s): %s",
        len(plan.will_delete),
        [str(i) for i in plan.will_delete],
    )

    # ── Step 3: Hard-delete Qdrant points ─────────────────────────────────────
    for del_id in plan.will_delete:
        try:
            await delete_point(del_id)
            logger.info("cascade_delete: Qdrant point deleted page_id=%s", del_id)
        except Exception as exc:  # noqa: BLE001
            # Non-fatal: Qdrant may not have the point (e.g. never embedded)
            logger.warning("cascade_delete: Qdrant delete failed page_id=%s: %s", del_id, exc)

    # ── Step 4: Apply wikilink rewrites (frontmatter-safe; body only; I5) ─────
    total_occurrences = 0
    files_written = 0
    for rewrite in plan.wikilinks_to_rewrite:
        abs_path = settings.vault_root / rewrite.file_path
        try:
            raw = abs_path.read_text(encoding="utf-8")
            # I5 / DEFECT-F13-002: rewrite the BODY only and preserve the YAML frontmatter
            # block BYTE-FOR-BYTE. We must NOT round-trip through frontmatter.loads/dumps here
            # — PyYAML's dumper reorders keys alphabetically and renormalises list indentation,
            # producing spurious git diffs on every vault file a delete touches.
            new_text = _rewrite_body_preserving_frontmatter(raw, plan.target_title or "")
            if new_text is None:
                logger.debug("cascade_delete: no changes to %s (body unchanged)", rewrite.file_path)
                continue
            abs_path.write_text(new_text, encoding="utf-8")
            total_occurrences += rewrite.occurrences
            files_written += 1
            logger.info(
                "cascade_delete: rewrote dead wikilinks in %s (occurrences=%d)",
                rewrite.file_path,
                rewrite.occurrences,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("cascade_delete: failed to rewrite %s: %s", rewrite.file_path, exc)

        # Re-persist links for the rewritten file (incremental, I1)
        try:
            await _repersist_links(rewrite.source_page_id, rewrite.file_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "cascade_delete: persist_links failed for %s: %s", rewrite.file_path, exc
            )

    # ── Step 5: Prune sources[] for will_preserve pages ───────────────────────
    if plan.will_preserve_with_pruned_source and plan.raw_source_to_delete:
        for preserve_id in plan.will_preserve_with_pruned_source:
            await _prune_sources(preserve_id, plan.raw_source_to_delete)

    # ── Step 6: Set dangling=True on residual link rows pointing at deleted pages ──
    async with get_session() as session:
        await session.execute(
            update(Link).where(Link.target_page_id.in_(plan.will_delete)).values(dangling=True)
        )

    # ── Step 7: Delete edge rows touching any deleted page ────────────────────
    async with get_session() as session:
        await session.execute(
            sa_delete(Edge).where(
                or_(
                    Edge.source_page_id.in_(plan.will_delete),
                    Edge.target_page_id.in_(plan.will_delete),
                )
            )
        )
    logger.info("cascade_delete: edge rows deleted for %d page(s)", len(plan.will_delete))

    # ── Step 8: Regenerate index.md ONCE (deleted pages auto-excluded; K3/I1) ──
    try:
        from app.wiki.index import update_index

        async with get_session() as idx_sess:
            await update_index(idx_sess, settings.vault_root)
        logger.info("cascade_delete: index.md regenerated")
        index_entry_removed = True
    except Exception as exc:  # noqa: BLE001
        logger.error("cascade_delete: update_index failed: %s", exc)
        index_entry_removed = False

    # ── Step 9: Delete raw/sources/ file from disk (AQ-v0.5-5) ───────────────
    if plan.raw_source_to_delete:
        raw_abs = settings.vault_root / plan.raw_source_to_delete
        try:
            raw_abs.unlink(missing_ok=True)
            logger.info("cascade_delete: raw source file deleted: %s", plan.raw_source_to_delete)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "cascade_delete: failed to delete raw source file %s: %s",
                plan.raw_source_to_delete,
                exc,
            )

    # ── Step 10: bump data_version EXACTLY ONCE (I2; AC-F13-4c) ─────────────
    new_version = await _bump_version_and_notify()
    logger.info("cascade_delete: data_version bumped to %d (EXACTLY ONCE per delete)", new_version)

    return CascadeResult(
        deleted_page_id=page_id,
        wikilinks_cleaned=total_occurrences,
        index_entry_removed=index_entry_removed,
        shared_entity_warnings=plan.shared_entity_warnings,
        files_written=files_written,
        data_version_after=new_version,
    )


# ── Private step helpers ───────────────────────────────────────────────────────


async def _repersist_links(source_page_id: uuid.UUID, file_path: str) -> None:
    """
    Re-parse wikilinks from the rewritten file and persist them (incremental, I1).

    Called after each successful dead-link rewrite so the links index stays correct.
    """
    from app.wiki.links import parse_wikilinks, persist_links

    abs_path = settings.vault_root / file_path
    try:
        raw = abs_path.read_text(encoding="utf-8")
        post = frontmatter.loads(raw)
        parsed = parse_wikilinks(post.content)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cascade_delete._repersist_links: cannot read %s: %s", file_path, exc)
        return

    async with get_session() as session:
        await persist_links(session, source_page_id, parsed)


async def _prune_sources(page_id: uuid.UUID, source_to_remove: str) -> None:
    """
    Remove *source_to_remove* from a page's frontmatter sources[] and update pages.sources JSONB.

    Frontmatter-safe: uses python-frontmatter loads/dumps round-trip (I5).
    """
    from sqlalchemy import select, update

    async with get_session() as session:
        row = await session.execute(
            select(Page.file_path, Page.sources).where(
                Page.id == page_id,
                Page.deleted_at.is_(None),
            )
        )
        r = row.first()
        if r is None:
            return
        file_path, sources = r

    if not file_path:
        return

    new_sources: list[str] = [s for s in (sources or []) if s != source_to_remove]

    # Update JSONB in DB
    async with get_session() as session:
        await session.execute(update(Page).where(Page.id == page_id).values(sources=new_sources))

    # Update frontmatter in file (frontmatter-safe; I5)
    abs_path = settings.vault_root / file_path
    try:
        raw = abs_path.read_text(encoding="utf-8")
        post = frontmatter.loads(raw)
        post.metadata["sources"] = new_sources
        # sources[] is intentionally mutated here, so byte-identity is impossible — but pass
        # sort_keys=False so PyYAML keeps the existing key ORDER instead of alphabetising it
        # (DEFECT-F13-002 / I5: minimise the git diff to just the sources change).
        abs_path.write_text(frontmatter.dumps(post, sort_keys=False), encoding="utf-8")
        logger.info(
            "cascade_delete._prune_sources: pruned %r from sources[] in %s",
            source_to_remove,
            file_path,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "cascade_delete._prune_sources: failed to update file %s: %s", file_path, exc
        )


async def _bump_version_and_notify() -> int:
    """
    Increment vault_state.data_version by 1 (EXACTLY ONCE per cascade_delete call — AC-F13-4c).

    Mirrors bump_version() in orchestrator.py but returns the new version integer for the
    CascadeResult and to pass to _graph_cache.notify_bump().

    NEVER calls GraphEngine.recompute() / FA2 inline (I2 / ADR-0026 §8 #2).
    """
    from sqlalchemy import select, update

    async with get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state = row.scalar_one_or_none()
        if state is None:
            state = VaultState(vault_id=settings.vault_id, data_version=1)
            state.updated_at = datetime.now(UTC)
            session.add(state)
            new_version = 1
        else:
            await session.execute(
                update(VaultState)
                .where(VaultState.vault_id == settings.vault_id)
                .values(
                    data_version=VaultState.data_version + 1,
                    updated_at=datetime.now(UTC),
                )
            )
            # Re-read the new value
            result = await session.execute(
                select(VaultState.data_version).where(VaultState.vault_id == settings.vault_id)
            )
            new_version = result.scalar_one_or_none() or 0

    # Notify GraphCache (debounced FA2 recompute fires on its own schedule — I2)
    # No-op if the cache has not been initialised (test environments without lifespan).
    try:
        from app.main import _graph_cache

        if _graph_cache is not None:
            _graph_cache.notify_bump(new_version)
    except Exception:  # noqa: BLE001
        logger.debug("cascade_delete: graph cache notify_bump skipped (cache not ready)")

    return new_version
