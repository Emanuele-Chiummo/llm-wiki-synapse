"""
Human-gated deterministic fix appliers (ADR-0037 §5). Called ONLY from ``apply_lint_fix``
(the human gate) — NEVER from the scan path. Each applier performs at most ONE
``data_version`` bump (I1) and edits ONLY the referencing/target page's BODY, never its
frontmatter (I5).

``contradiction`` is the one category whose apply step makes a bounded, optional provider
call (to phrase the open-question page) — it rides ``app.ops._llm`` directly (I6/I7) with a
deterministic template fallback; it is NOT part of the ``semantic.py`` scan-time LLM pass.

``_APPLY_HANDLERS`` is the category → handler registry ``apply_lint_fix`` (in ``__init__``)
dispatches through, replacing the former if/elif chain.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.config import settings
from app.db import get_session
from app.ingest.schemas import PageType
from app.models import LintFinding, Page
from app.ops._llm import bounded_chat_collect, clean_str, resolve_operation_provider

logger = logging.getLogger(__name__)

# ── Stub-page type heuristic (ADR-0067 D1) ─────────────────────────────────────
# Legal organisation suffixes (case-insensitive).
_LEGAL_SUFFIX_RE: re.Pattern[str] = re.compile(
    r"\b(Inc\.?|Ltd\.?|Corp\.?|S\.p\.A\.?|GmbH|PRIVATE\s+LIMITED|LLC|PLC|LLP)\b",
    re.IGNORECASE,
)
# All-caps acronym token: ≥2 consecutive uppercase ASCII letters (e.g. "AWS", "NATO").
_ALL_CAPS_TOKEN_RE: re.Pattern[str] = re.compile(r"\b[A-Z]{2,}\b")

# Same threshold as run_lint_scan (ADR-0009 §3 / ADR-0037 §4).
_COST_ANOMALY_THRESHOLD_USD: float = 1.00


def _title_from_description(description: str) -> str | None:
    """Best-effort title extraction from a description for missing-page apply fallback."""
    # Look for a quoted phrase first.
    for quote in ("'", '"', "“", "”"):
        if quote in description:
            parts = description.split(quote)
            if len(parts) >= 3 and parts[1].strip():
                return parts[1].strip()
    return None


# ── Shared apply helper — append wikilink under ## Related heading (L4) ──────────


def _append_wikilink_to_body(body: str, link_target: str) -> str:
    """
    Append ``- [[link_target]]`` under the ``## Related`` heading in *body*, creating the
    heading if absent. Idempotent: no-ops if the link already exists (L4/I5).

    Port of lint-fixes.ts::appendWikilink.
    """
    # Idempotency: skip if the link already exists anywhere in the body.
    link_norm = link_target.lower()
    for m in re.finditer(r"\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]", body):
        if m.group(1).strip().lower() == link_norm:
            return body  # already present

    link_line = f"- [[{link_target}]]"
    heading_match = re.search(r"^##\s+Related\s*$", body, re.IGNORECASE | re.MULTILINE)
    if heading_match:
        insert_at = heading_match.end()
        return body[:insert_at] + "\n" + link_line + body[insert_at:]
    # No ## Related heading → append one at the end of the body.
    return body.rstrip("\n") + "\n\n## Related\n" + link_line + "\n"


async def _read_page_file_for_apply(
    page_id_str: str,
) -> tuple[str, str, str, str, bool] | None:
    """
    Load a page for the apply path and return (file_path, abs_path_str, fm_block, body, have_fm).

    Returns None when the page no longer exists (caller raises 404/502 as appropriate).
    Uses portable CAST(id AS TEXT) for SQLite/Postgres parity.
    """
    from sqlalchemy import text as sa_text

    async with get_session() as session:
        row = (
            await session.execute(
                sa_text(
                    "SELECT id, vault_id, file_path, title "
                    "FROM pages WHERE CAST(id AS TEXT) = :pid AND deleted_at IS NULL"
                ).bindparams(pid=page_id_str)
            )
        ).first()

    if row is None:
        return None

    file_path: str = row.file_path
    abs_path = settings.vault_root / file_path

    try:
        raw = abs_path.read_text(encoding="utf-8")
    except OSError:
        return None

    if raw.startswith("---\n"):
        parts = raw.split("---\n", maxsplit=2)
        if len(parts) == 3:
            return file_path, str(abs_path), parts[1], parts[2], True

    return file_path, str(abs_path), "", raw, False


async def _write_body_back(
    *,
    file_path: str,
    abs_path_str: str,
    fm_block: str,
    new_body: str,
    have_frontmatter: bool,
    source_page_id: uuid.UUID,
) -> None:
    """
    Write the updated body back to disk, re-persist links, and bump data_version once (I1/I5).
    Used by _apply_no_outlinks and _apply_orphan_page.
    """
    import frontmatter as _fm
    from fastapi import HTTPException

    from app.wiki.links import parse_wikilinks, persist_links

    new_raw = ("---\n" + fm_block + "---\n" + new_body) if have_frontmatter else new_body

    try:
        import pathlib

        pathlib.Path(abs_path_str).write_text(new_raw, encoding="utf-8")
    except OSError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"apply write failed for {file_path}: {exc}",
        ) from exc

    try:
        post = _fm.loads(new_raw)
        parsed = parse_wikilinks(post.content)
        async with get_session() as session:
            await persist_links(session, source_page_id, parsed)
    except Exception as exc:  # noqa: BLE001
        logger.warning("_write_body_back: persist_links failed for %s: %s", file_path, exc)

    try:
        from app.ingest.orchestrator import bump_version

        await bump_version()
    except Exception as exc:  # noqa: BLE001
        logger.warning("_write_body_back: bump_version failed: %s", exc)


# ── Apply seams (ADR-0037 §5) ───────────────────────────────────────────────────


async def _apply_broken_wikilink(finding: LintFinding) -> str:
    """
    Apply a broken-wikilink fix (L3 / ADR-0037 B1 / I1/I5).

    When a suggestion exists (finding.suggested_target is not None):
      1. Load the referencing page file (finding.target_page_id is the REFERENCING page).
      2. Rewrite occurrences of [[old]] and [[old|label]] to [[Suggested]] / [[Suggested|label]]
         in the BODY ONLY (split on leading --- frontmatter fence — I5).
      3. Write the file, re-run persist_links, bump data_version ONCE (I1).

    When no suggestion exists → create a stub page for the missing target via
    _create_broken_link_stub (L4 / ADR-0058 §L4) — NOT a flag-only acknowledgement.

    Raises:
      HTTPException(404) — referencing page no longer exists.
      HTTPException(409) — finding has no target_page_id (defensive).
      HTTPException(502) — file write / link persist failed.
    """
    import re as _re

    from fastapi import HTTPException
    from sqlalchemy import text as sa_text

    # ── No suggestion → create a stub page for the missing target (L4/ADR-0058 §L4) ──
    if not finding.suggested_target:
        return await _create_broken_link_stub(finding)

    if finding.target_page_id is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "broken-wikilink apply failed: the finding carries no referencing page id. "
                "Dismiss it or re-run lint."
            ),
        )

    old_target = finding.target_title or ""
    new_target = finding.suggested_target

    if not old_target:
        return (
            f"broken-wikilink: target_title empty; acknowledged. " f"Suggestion was {new_target!r}."
        )

    # ── Load the referencing page ─────────────────────────────────────────────────
    async with get_session() as session:
        row = (
            await session.execute(
                sa_text(
                    "SELECT id, vault_id, file_path, title "
                    "FROM pages WHERE CAST(id AS TEXT) = :pid AND deleted_at IS NULL"
                ).bindparams(pid=str(finding.target_page_id))
            )
        ).first()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "broken-wikilink apply failed: the referencing page no longer exists. "
                "Finding left open — dismiss or re-run lint."
            ),
        )

    file_path: str = row.file_path
    abs_path = settings.vault_root / file_path

    # ── Read + split frontmatter / body (I5 — NEVER touch frontmatter) ───────────
    try:
        raw = abs_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"broken-wikilink apply failed: cannot read {file_path}: {exc}",
        ) from exc

    if raw.startswith("---\n"):
        parts = raw.split("---\n", maxsplit=2)
        if len(parts) == 3:
            fm_block, body = parts[1], parts[2]
            have_frontmatter = True
        else:
            fm_block, body = "", raw
            have_frontmatter = False
    else:
        fm_block, body = "", raw
        have_frontmatter = False

    # ── Anchored regex rewrite in body only ───────────────────────────────────────
    # Match [[old_target]] and [[old_target|label]] (escaped for regex safety).
    old_escaped = _re.escape(old_target)
    pattern = _re.compile(r"\[\[" + old_escaped + r"(?:\|([^\[\]]*))?\]\]")

    def _replace(m: _re.Match[str]) -> str:
        label = m.group(1)  # None if no alias
        if label is not None:
            return f"[[{new_target}|{label}]]"
        return f"[[{new_target}]]"

    new_body = pattern.sub(_replace, body)

    if new_body == body:
        return (
            f"broken-wikilink: no occurrences of [[{old_target}]] found in body of {file_path!r}; "
            "acknowledged without edit."
        )

    # ── Write the file back (I5 — frontmatter preserved byte-for-byte) ───────────
    if have_frontmatter:
        new_raw = "---\n" + fm_block + "---\n" + new_body
    else:
        new_raw = new_body

    try:
        abs_path.write_text(new_raw, encoding="utf-8")
    except OSError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"broken-wikilink apply failed: cannot write {file_path}: {exc}",
        ) from exc

    # ── Re-persist links for the rewritten file (I1) ──────────────────────────────
    try:
        import frontmatter as _fm

        from app.wiki.links import parse_wikilinks, persist_links

        post = _fm.loads(new_raw)
        parsed = parse_wikilinks(post.content)
        async with get_session() as session:
            await persist_links(session, uuid.UUID(str(finding.target_page_id)), parsed)
    except Exception as exc:  # noqa: BLE001
        logger.warning("_apply_broken_wikilink: persist_links failed for %s: %s", file_path, exc)

    # ── Bump data_version ONCE (I1) ───────────────────────────────────────────────
    try:
        from app.ingest.orchestrator import bump_version

        await bump_version()
    except Exception as exc:  # noqa: BLE001
        logger.warning("_apply_broken_wikilink: bump_version failed: %s", exc)

    return (
        f"broken-wikilink: rewrote [[{old_target}]] → [[{new_target}]] "
        f"in body of {file_path!r} (data_version bumped once, I1)."
    )


async def _apply_missing_xref(finding: LintFinding) -> str:
    """
    Apply a missing-xref fix by reusing the wikilink-enrichment seam (I1/I5).

    Runs the bounded ops/enrich_wikilinks.enrich_wikilinks pass over the referencing page,
    which adds [[target]] links into the BODY only and bumps data_version ONCE (I1). The pass
    is provider-agnostic (I6) and fully bounded (I7). Returns a resolution note.
    """
    from fastapi import HTTPException

    from app.ops.enrich_wikilinks import enrich_wikilinks

    if finding.target_page_id is None:
        # No concrete referencing page → fall back to flag-only acknowledgement.
        return (
            "missing-xref: no referencing page recorded; acknowledged without edit "
            "(re-run lint after editing)."
        )

    # Load the referencing page by id. CAST to text for SQLite/Postgres parity (mirrors
    # graph/engine.py) so the lookup works regardless of the id column's native type.
    from sqlalchemy import text as sa_text

    async with get_session() as session:
        row = (
            await session.execute(
                sa_text(
                    "SELECT id, vault_id, file_path, title, type AS page_type "
                    "FROM pages WHERE CAST(id AS TEXT) = :pid"
                ).bindparams(pid=str(finding.target_page_id))
            )
        ).first()
    page = None
    if row is not None:
        page = Page(
            id=uuid.UUID(str(row.id)),
            vault_id=row.vault_id,
            file_path=row.file_path,
            title=row.title,
            page_type=row.page_type,
            content_hash="",
        )

    if page is None:
        raise HTTPException(
            status_code=502,
            detail=(
                "missing-xref apply failed: the referencing page no longer exists. "
                "Finding left open — dismiss or re-run lint."
            ),
        )

    result = await enrich_wikilinks([page], finding.vault_id)
    return (
        f"missing-xref: ran wikilink-enrichment over {page.title!r} — "
        f"links_added={result.links_added} (data_version bumped once on edit, I1)."
    )


async def _apply_missing_page(finding: LintFinding) -> str:
    """
    Apply a missing-page fix by delegating to the lazy-generation seam used by
    review.create_page_from_review (ADR-0034 §5) — bounded orchestrated loop, one
    data_version bump via write_wiki_page (I1). Provider-agnostic (I6).
    """
    from fastapi import HTTPException

    from app.ingest.orchestrator import write_wiki_page
    from app.ops.review import _run_generation
    from app.provider_config_service import ConfigNotFoundError, resolve_provider_config

    title = finding.target_title or _title_from_description(finding.description)
    if not title:
        raise HTTPException(
            status_code=409,
            detail=(
                "missing-page apply failed: the finding carries no target title to create. "
                "Dismiss it or edit the wiki manually."
            ),
        )

    # Resolve the ingest provider (I6 — 409 if none configured).
    try:
        provider_config_row = await resolve_provider_config("ingest", finding.vault_id)
    except ConfigNotFoundError as cnfe:
        raise HTTPException(
            status_code=409,
            detail=(
                "No ingest provider configured for this vault. Configure a provider before "
                "applying a missing-page fix (I6)."
            ),
        ) from cnfe

    origin_source = f"lint:{finding.id}"
    try:
        # Capability-aware (I6): _run_generation delegates to an agentic provider (which writes
        # the page itself via MCP write_page) or runs the orchestrated loop (returning a WikiPage
        # this caller writes once). Exactly one write per page either way (I1) — never double.
        outcome = await _run_generation(
            vault_id=finding.vault_id,
            proposed_title=title,
            proposed_page_type=None,  # heuristic at generation time (ADR-0034 §5.2)
            rationale=finding.description,
            origin_source=origin_source,
            provider_config_row=provider_config_row,
        )
        if outcome.created_page_id is not None:
            # Delegated route: the agent already wrote the page — do NOT write again (I1).
            created_page_id = outcome.created_page_id
        elif outcome.wiki_page is not None:
            # Orchestrated route: write the produced page once via the single incremental seam.
            created_page = await write_wiki_page(None, outcome.wiki_page, origin_source)
            created_page_id = str(created_page.id)
        else:
            # Defensive: _run_generation raises rather than returning an empty outcome.
            raise RuntimeError("page generation produced no page")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "_apply_missing_page: generation/write failed for finding=%s: %s — left open",
            finding.id,
            exc,
        )
        raise HTTPException(
            status_code=502,
            detail=(f"missing-page apply failed: {exc}. Finding left open — retry or dismiss."),
        ) from exc

    return (
        f"missing-page: created page {title!r} (page_id={created_page_id}; "
        "one data_version bump, I1)."
    )


async def _apply_no_outlinks(finding: LintFinding) -> str:
    """
    Apply a no-outlinks fix (L4 / ADR-0058 §L4 / I1/I5).

    Appends ``- [[suggested_target]]`` under ``## Related`` in the finding's page body
    (creates the heading if absent). Idempotent: no-ops if the link already exists.
    Re-persists links and bumps data_version ONCE (I1). Body-only edit (I5).

    Falls back to flag-only acknowledgement when no suggested_target is recorded.
    """
    from fastapi import HTTPException

    if not finding.suggested_target:
        return (
            "no-outlinks: no suggested target available; acknowledged as flag-only. "
            "Add a [[wikilink]] to the page manually."
        )

    if finding.target_page_id is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "no-outlinks apply failed: the finding carries no target page id. "
                "Dismiss it or re-run lint."
            ),
        )

    result = await _read_page_file_for_apply(str(finding.target_page_id))
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "no-outlinks apply failed: the target page no longer exists. "
                "Finding left open — dismiss or re-run lint."
            ),
        )

    file_path, abs_path_str, fm_block, body, have_frontmatter = result
    new_body = _append_wikilink_to_body(body, finding.suggested_target)

    if new_body == body:
        return (
            f"no-outlinks: [[{finding.suggested_target}]] already present in {file_path!r}; "
            "acknowledged without edit."
        )

    await _write_body_back(
        file_path=file_path,
        abs_path_str=abs_path_str,
        fm_block=fm_block,
        new_body=new_body,
        have_frontmatter=have_frontmatter,
        source_page_id=uuid.UUID(str(finding.target_page_id)),
    )
    return (
        f"no-outlinks: appended [[{finding.suggested_target}]] under ## Related in "
        f"{file_path!r} (data_version bumped once, I1)."
    )


async def _apply_orphan_page(finding: LintFinding) -> str:
    """
    Apply an orphan-page fix (L4 / ADR-0058 §L4 / I1/I5).

    When finding.suggested_page_id is set: appends ``- [[<orphan title>]]`` under
    ``## Related`` in the SUGGESTED SOURCE PAGE (the page that should link to the orphan).
    Re-persists links and bumps data_version ONCE (I1). Body-only edit (I5).

    Falls back to flag-only acknowledgement when no suggested_page_id is recorded,
    matching the pre-L4 behaviour for suggestion-less orphan-page findings.
    """
    from fastapi import HTTPException

    if not finding.suggested_page_id:
        return (
            "orphan-page: no suggested source page available; acknowledged as flag-only. "
            "Add a [[wikilink]] to this page from another page manually."
        )

    orphan_title = finding.target_title or "untitled"

    result = await _read_page_file_for_apply(str(finding.suggested_page_id))
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "orphan-page apply failed: the suggested source page no longer exists. "
                "Finding left open — dismiss or re-run lint."
            ),
        )

    file_path, abs_path_str, fm_block, body, have_frontmatter = result
    new_body = _append_wikilink_to_body(body, orphan_title)

    if new_body == body:
        return (
            f"orphan-page: [[{orphan_title}]] already present in suggested source "
            f"{file_path!r}; acknowledged without edit."
        )

    await _write_body_back(
        file_path=file_path,
        abs_path_str=abs_path_str,
        fm_block=fm_block,
        new_body=new_body,
        have_frontmatter=have_frontmatter,
        source_page_id=uuid.UUID(str(finding.suggested_page_id)),
    )
    return (
        f"orphan-page: appended [[{orphan_title}]] under ## Related in suggested source "
        f"{file_path!r} (data_version bumped once, I1)."
    )


def _infer_stub_page_type(target_title: str) -> PageType:
    """
    Derive the correct PageType for a broken-wikilink stub WITHOUT an LLM call (ADR-0067 D1).

    Rules applied cheapest-first:
      1. Title contains a legal organisation suffix (Inc./Ltd./Corp./S.p.A./GmbH/
         "PRIVATE LIMITED"/LLC/PLC/LLP) → ENTITY (organisation/company name).
      2. Title contains an all-caps token of ≥2 letters (e.g. "AWS", "NATO", "GDPR")
         → ENTITY (acronym-style proper noun).
      3. Any individual word starts with an uppercase letter
         → ENTITY (proper noun / product name / title-cased term).
      4. Default → CONCEPT (common-noun phrase, technical term, abstract idea).

    NEVER returns PageType.QUERY — queries/ is reserved for genuine open questions
    (ADR-0067 D1; LN-D1 fix).
    """
    title = target_title.strip()
    if not title:
        return PageType.CONCEPT

    # Rule 1: legal suffix → organisation → entity
    if _LEGAL_SUFFIX_RE.search(title):
        return PageType.ENTITY

    # Rule 2: all-caps acronym token (≥2 uppercase letters) → entity
    if _ALL_CAPS_TOKEN_RE.search(title):
        return PageType.ENTITY

    # Rule 3: any word starts with an uppercase letter → proper noun → entity
    if any(word and word[0].isupper() for word in title.split()):
        return PageType.ENTITY

    # Rule 4: default → concept
    return PageType.CONCEPT


async def _create_broken_link_stub(finding: LintFinding) -> str:
    """
    Create a stub page for a broken-wikilink finding that has no suggested_target (L4).

    Writes a typed stub page (entity or concept — NEVER query; ADR-0067 D1) via the
    normal write_wiki_page seam, then re-resolves links for the referencing page so the
    previously-dangling link connects to the new stub.  One data_version bump (I1).

    The page type is inferred deterministically from the broken target text by
    _infer_stub_page_type (no LLM call).  Legal-suffix/all-caps → entity; proper noun
    → entity; common phrase → concept.  queries/ is NEVER used for stubs.

    Port of lint-fixes.ts::ensureBrokenLinkStub.
    Falls back to flag-only acknowledgement on any failure (502 path).
    """
    from fastapi import HTTPException

    broken_target = finding.target_title or ""
    if not broken_target:
        return (
            "broken-wikilink: no broken target title recorded; acknowledged as flag-only. "
            "Dismiss and re-run lint."
        )

    # Derive a stub title from the broken target text.
    stub_title = (
        broken_target.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ").strip()
        or "Missing Page"
    )

    from app.ingest.orchestrator import write_wiki_page
    from app.ingest.schemas import WikiFrontmatter, WikiPage

    stub_type = _infer_stub_page_type(stub_title)

    stub_page = WikiPage(
        title=stub_title,
        type=stub_type,
        content=(
            f"# {stub_title}\n\n"
            "Stub created by Wiki Lint for a referenced but not-yet-written page. "
            "Enrich or merge.\n"
        ),
        frontmatter=WikiFrontmatter(
            type=stub_type,
            title=stub_title,
            sources=[f"lint:{finding.id}"],
            lang="en",
            tags=["stub", "lint"],
        ),
    )

    try:
        created_page = await write_wiki_page(None, stub_page, f"lint:{finding.id}")
        created_page_id = str(created_page.id)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "_create_broken_link_stub: write_wiki_page failed for %r: %s — left open",
            stub_title,
            exc,
        )
        raise HTTPException(
            status_code=502,
            detail=(
                f"broken-wikilink stub creation failed for {stub_title!r}: {exc}. "
                "Finding left open — retry or dismiss."
            ),
        ) from exc

    # Re-resolve links for the referencing page so the now-existing stub connects.
    if finding.target_page_id is not None:
        try:
            from app.wiki.links import reresolve_dangling_links

            async with get_session() as session:
                reconnected = await reresolve_dangling_links(session)
            logger.debug(
                "_create_broken_link_stub: reresolve_dangling_links reconnected %d links",
                reconnected,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("_create_broken_link_stub: reresolve_dangling_links failed: %s", exc)

    from app.ingest.schemas import type_subdir

    stub_subdir = type_subdir(stub_type)
    return (
        f"broken-wikilink: created stub page {stub_title!r} (type={stub_type.value}, "
        f"page_id={created_page_id}) under {stub_subdir}/ "
        f"(data_version bumped once via write_wiki_page, I1)."
    )


# ── contradiction → open-question query authoring (ADR-0067 D4/P0-4) ─────────────


@dataclass
class _ContradictionPage:
    """One live page a contradiction finding concerns (resolved from the finding)."""

    page_id: str
    title: str
    slug: str  # on-disk file stem == the `related:` slug write_wiki_page emits
    sources: list[str]  # DB `pages.sources` — unioned into the query page's sources[]


def _parse_sources(raw: Any) -> list[str]:
    """
    Coerce a `pages.sources` cell to a clean list[str]. Portable across the Postgres runtime
    (JSONB → list) and the SQLite test harness (Text → JSON string). Blanks dropped.
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(s).strip() for s in raw if s and str(s).strip()]
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        try:
            parsed = json.loads(s)
        except (json.JSONDecodeError, ValueError):
            return [s]
        if isinstance(parsed, list):
            return [str(x).strip() for x in parsed if x and str(x).strip()]
        return [str(parsed).strip()] if parsed and str(parsed).strip() else []
    return []


def _contradiction_candidate_titles(finding: LintFinding) -> list[str]:
    """
    Ordered, de-duplicated candidate page titles the contradiction concerns. A contradiction
    finding names the two conflicting pages in `target_title` (page A) and in its `description`
    (the LLM phrases it as "[[A]] claims X but [[B]] claims Y"). We harvest, in order: the
    target_title, then any `[[wikilink]]` targets in the description, then any quoted titles.
    """
    from app.wiki.links import parse_wikilinks

    candidates: list[str] = []
    seen: set[str] = set()

    def _add(t: str | None) -> None:
        if not t:
            return
        t = t.strip()
        if not t or t.lower() in seen:
            return
        seen.add(t.lower())
        candidates.append(t)

    _add(finding.target_title)
    for pl in parse_wikilinks(finding.description or ""):
        _add(pl.target)
    for quoted in re.findall(r"[\"'“”‘’]([^\"'“”‘’]{2,})[\"'“”‘’]", finding.description or ""):
        _add(quoted)
    return candidates


async def _resolve_contradiction_pages(finding: LintFinding) -> list[_ContradictionPage]:
    """
    Resolve up to TWO distinct live pages the contradiction concerns (I1 — indexed reads only).

    Uses the shared tolerant title resolver (exact → case-insensitive → slug) over the candidate
    titles, then fetches each hit's file_path (→ slug) and `pages.sources` (→ union into the query
    page's sources[]). Returns [] when nothing resolves (caller still writes a valid page).
    """
    from pathlib import Path

    from sqlalchemy import text as sa_text

    from app.wiki.links import resolve_suggested_target

    out: list[_ContradictionPage] = []
    seen_ids: set[str] = set()
    async with get_session() as session:
        for title in _contradiction_candidate_titles(finding):
            hit = await resolve_suggested_target(title, session)
            if hit is None:
                continue
            page_id, matched_title = hit
            pid = str(page_id)
            if pid in seen_ids:
                continue
            row = (
                await session.execute(
                    sa_text(
                        "SELECT file_path, sources FROM pages "
                        "WHERE CAST(id AS TEXT) = :pid AND deleted_at IS NULL"
                    ).bindparams(pid=pid)
                )
            ).first()
            if row is None or not row.file_path:
                continue
            seen_ids.add(pid)
            out.append(
                _ContradictionPage(
                    page_id=pid,
                    title=matched_title,
                    slug=Path(row.file_path).stem,
                    sources=_parse_sources(row.sources),
                )
            )
            if len(out) >= 2:
                break
    return out


def _deterministic_contradiction_copy(
    pages: list[_ContradictionPage], description: str
) -> dict[str, Any]:
    """
    Deterministic (no-LLM) fallback copy for the query page (never fails the apply — I7).
    Returns {question, question_body, hypothesis, open_points[], impact}.
    """
    if len(pages) >= 2:
        a, b = pages[0].title, pages[1].title
        question = f"How should the conflict between {a} and {b} be resolved?"
        q_body = (
            f"[[{a}]] and [[{b}]] make claims that appear to conflict. "
            f"{description.strip()}".strip()
        )
    elif len(pages) == 1:
        a = pages[0].title
        question = f"Is the claim in {a} consistent with the rest of the wiki?"
        q_body = f"[[{a}]] carries a claim flagged as contradictory. {description.strip()}".strip()
    else:
        question = "How should this contradiction be resolved?"
        q_body = description.strip() or "A contradiction was flagged across the wiki."
    return {
        "question": question,
        "question_body": q_body,
        "hypothesis": (
            "One of the conflicting statements is out of date, scoped to a different context, "
            "or measured differently; reconcile the definitions/timeframes before deciding."
        ),
        "open_points": [
            "Which source is authoritative / most recent?",
            "Are the two claims actually about the same scope, or different contexts?",
            "What evidence would settle the conflict?",
        ],
        "impact": (
            "Until resolved, downstream pages may cite conflicting facts and mislead readers."
        ),
    }


async def _phrase_contradiction_query(
    finding: LintFinding, pages: list[_ContradictionPage]
) -> dict[str, Any]:
    """
    Phrase the open-question copy for a contradiction (I6/I7). Makes AT MOST ONE bounded provider
    chat() call (resolved via resolve_provider_config('ingest'), wrapped in wait_for) asking for a
    JSON object {question, question_body, hypothesis, open_points[], impact}. Binds a run-scoped
    UsageAccumulator and logs total_cost_usd. ANY failure (no provider, timeout, bad JSON) →
    deterministic template (never raises — the apply must not fail, K8/I7).
    """
    import asyncio

    from app.ops._llm import loads_json_lenient

    fallback = _deterministic_contradiction_copy(pages, finding.description or "")

    resolved = await resolve_operation_provider(finding.vault_id)
    if resolved is None:
        logger.info(
            "_phrase_contradiction_query: no ingest provider — deterministic template (I6)."
        )
        return fallback
    provider, config_row = resolved

    titles = " vs ".join(p.title for p in pages) if pages else "(unresolved pages)"
    excerpt_block = "\n".join(f"- {p.title}: (see page)" for p in pages) or "(none)"
    instruction = (
        "You are the LINT step of a self-organizing wiki. A CONTRADICTION was flagged between "
        "wiki pages. Phrase it as ONE neutral open research QUESTION (a genuine query page — "
        "Karpathy queries/), NOT a fix.\n\n"
        f"# Conflicting pages\n{titles}\n\n"
        f"# Page notes\n{excerpt_block}\n\n"
        f"# Contradiction description\n{(finding.description or '').strip()}\n\n"
        "Respond in the SAME LANGUAGE as the page titles/description. Return ONLY a JSON object "
        "with keys:\n"
        '  "question": a single interrogative sentence ENDING WITH "?" (the page title),\n'
        '  "question_body": 1-3 sentences framing the conflict,\n'
        '  "hypothesis": 1-2 sentences proposing a likely reconciliation,\n'
        '  "open_points": a list of 2-5 short strings,\n'
        '  "impact": 1 sentence on why resolving it matters.\n'
        "No prose outside the JSON object."
    )

    from app.ingest.provider.base import UsageAccumulator

    accumulator = UsageAccumulator()
    try:
        provider.bind_accumulator(accumulator)
    except Exception as exc:  # noqa: BLE001 — accumulator binding is best-effort
        logger.debug("_phrase_contradiction_query: bind_accumulator failed: %s", exc)

    timeout_s = float(getattr(settings, "lint_timeout_seconds", 30.0))
    degraded = False
    raw = ""
    try:
        raw = await asyncio.wait_for(bounded_chat_collect(provider, instruction), timeout=timeout_s)
    except (
        Exception
    ) as exc:  # noqa: BLE001 — degrade to template, never fail apply (TimeoutError too)
        logger.warning(
            "_phrase_contradiction_query: provider call failed (%s) — deterministic template.",
            exc,
        )
        degraded = True
    finally:
        logger.info(
            "contradiction query provider call: tokens=%d cost_usd=%.4f calls=%d finding=%s",
            accumulator.total_tokens,
            round(accumulator.total_cost_usd, 4),
            accumulator.calls,
            finding.id,
        )
        if accumulator.total_cost_usd > _COST_ANOMALY_THRESHOLD_USD:
            logger.warning(
                "COST ANOMALY: contradiction query finding=%s total_cost_usd=%.4f exceeds $%.2f",
                finding.id,
                accumulator.total_cost_usd,
                _COST_ANOMALY_THRESHOLD_USD,
            )

    if degraded:
        return fallback

    parsed = loads_json_lenient(raw)
    if not isinstance(parsed, dict):
        return fallback

    question = clean_str(parsed.get("question")) or fallback["question"]
    if not question.rstrip().endswith("?"):
        question = question.rstrip().rstrip(".") + "?"
    open_points_raw = parsed.get("open_points")
    open_points = (
        [s.strip() for s in open_points_raw if isinstance(s, str) and s.strip()]
        if isinstance(open_points_raw, list)
        else []
    ) or fallback["open_points"]
    return {
        "question": question,
        "question_body": clean_str(parsed.get("question_body")) or fallback["question_body"],
        "hypothesis": clean_str(parsed.get("hypothesis")) or fallback["hypothesis"],
        "open_points": open_points,
        "impact": clean_str(parsed.get("impact")) or fallback["impact"],
    }


async def _apply_contradiction(finding: LintFinding) -> str:
    """
    Apply a contradiction finding by AUTHORING a genuine open-question `type=query` page
    (ADR-0067 D4/P0-4). This is the ONLY sanctioned query generator besides chat save-to-wiki;
    the ingest generation prohibition on `query` as free provider output is UNTOUCHED — this is
    an internal PIPELINE writer, not free model output.

    Shape (LLM Wiki query parity): question TITLE + body sections
    ## Question / ## Hypothesis / ## Open Points / ## Impact / ## References, with the two
    conflicting pages wikilinked under ## References (→ write_wiki_page emits related[]=both
    slugs), and DB sources[] = union of both pages' sources (no synthetic `lint:` source).

    Human-gated (K8 — the apply action IS the gate). Bounded provider call (I6/I7) with a
    deterministic template fallback (never fails the apply). ONE data_version bump —
    write_wiki_page owns it (I1).
    """
    from fastapi import HTTPException

    from app.ingest.orchestrator import write_wiki_page
    from app.ingest.schemas import PageType, WikiFrontmatter, WikiPage

    pages = await _resolve_contradiction_pages(finding)
    copy = await _phrase_contradiction_query(finding, pages)

    references = "\n".join(f"- [[{p.title}]]" for p in pages) or "- (conflicting pages)"
    open_points = "\n".join(f"- {pt}" for pt in copy["open_points"])
    body = (
        f"# {copy['question']}\n\n"
        f"## Question\n{copy['question_body']}\n\n"
        f"## Hypothesis\n{copy['hypothesis']}\n\n"
        f"## Open Points\n{open_points}\n\n"
        f"## Impact\n{copy['impact']}\n\n"
        f"## References\n{references}\n"
    )

    # DB sources[] = union of both pages' sources (ADR-0067 D4 — real raw docs, not `lint:`).
    union_sources: list[str] = []
    for p in pages:
        for s in p.sources:
            if s not in union_sources:
                union_sources.append(s)
    related_slugs = [p.slug for p in pages]

    query_page = WikiPage(
        title=copy["question"],
        type=PageType.QUERY,
        content=body,
        frontmatter=WikiFrontmatter(
            type=PageType.QUERY,
            title=copy["question"],
            sources=union_sources,
            related=related_slugs,
            tags=["open-question", "contradiction"],
        ),
    )

    try:
        # origin_source="" so write_wiki_page injects NO synthetic source — DB sources[] stays the
        # clean union of both pages' raw docs (ADR-0067 D4). The writer owns the single bump (I1).
        created = await write_wiki_page(None, query_page, "")
        created_id = str(created.id)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "_apply_contradiction: write_wiki_page failed for %r: %s — left open",
            copy["question"],
            exc,
        )
        raise HTTPException(
            status_code=502,
            detail=(
                f"contradiction apply failed writing query page {copy['question']!r}: {exc}. "
                "Finding left open — retry or dismiss."
            ),
        ) from exc

    linked = " & ".join(f"[[{p.title}]]" for p in pages) or "(no live pages resolved)"
    return (
        f"contradiction: authored open-question page {copy['question']!r} (type=query, "
        f"page_id={created_id}) under queries/ linking {linked}; DB sources[] unioned from "
        f"both pages ({len(union_sources)} source(s)). One data_version bump via write_wiki_page."
    )


# ── Category → handler registry (replaces the former if/elif dispatch chain) ─────
# Flag-only categories (stale-claim/suggestion) and the suggestion-dependent fallback for
# no-outlinks/orphan-page are handled inline by the callers of this registry (their handler
# functions above already degrade to a flag-only acknowledgement when no suggestion exists).

_APPLY_HANDLERS: dict[str, Callable[[LintFinding], Awaitable[str]]] = {
    "contradiction": _apply_contradiction,
    "broken-wikilink": _apply_broken_wikilink,
    "missing-xref": _apply_missing_xref,
    "missing-page": _apply_missing_page,
    "no-outlinks": _apply_no_outlinks,
    "orphan-page": _apply_orphan_page,
}
