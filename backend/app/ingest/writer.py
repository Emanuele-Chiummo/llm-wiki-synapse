"""Wiki page writer — moved from orchestrator.py (1.7.0 PR2).

The single shared write path (``write_wiki_page``) and its private helpers:
slug/filename + source-identity derivation, entity canonical-key merge, ``related``
resolution, defensive frontmatter stripping. Behaviour is unchanged; patched /
orchestrator-resident primitives are reached via ``orch.<name>`` so
``app.ingest.orchestrator`` remains the single monkeypatch surface.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import frontmatter

import app.ingest.orchestrator as orch
from app.config import settings
from app.ingest.provider.base import InferenceProvider
from app.ingest.schemas import PageType, WikiPage, type_subdir
from app.models import Page

logger = logging.getLogger(__name__)


# ── Wiki page writer (reused by the MCP write_page tool — ADR-0010 §2) ─────────


def _is_owned_only_by_source(prior_sources: list[str] | None, origin_source: str) -> bool:
    """
    True when an existing page's ONLY prior source is *origin_source* (nashsu/llm_wiki
    ``isOwnedOnlyBySource``). Such a page is "owned" by the source being re-ingested, so a
    correction/retraction must REPLACE its body rather than LLM-merge stale facts back in.

    Returns False when the page has NO recorded prior sources (unknown ownership — keep the
    safe merge) or when ANOTHER source also contributed (genuine multi-source page — merge so
    no source's content is lost).
    """
    if not origin_source:
        return False
    prior = {s for s in (prior_sources or []) if s}
    return bool(prior) and prior <= {origin_source}


def _strip_leading_frontmatter(body: str) -> str:
    """
    Defensively remove ONE stray leading YAML frontmatter block from a page *body*.

    The write path composes the file as `serialized frontmatter + body` (ADR-0011 —
    content excludes frontmatter). Some providers (notably the CLI agent via the MCP
    write_page tool) ignore that contract and pass a `content` that ALREADY begins with a
    `---\\n...\\n---` block, which would then be duplicated. This strips exactly one such
    leading block so the composed file has a single frontmatter block.

    Rules (conservative — never corrupt legitimate content):
      * If, after optional leading blank lines, the body does NOT start with a line that is
        exactly `---`, it is returned unchanged.
      * Otherwise the NEXT line that is exactly `---` or `...` (a YAML document terminator)
        closes the block; everything through that fence — plus any immediately following
        blank lines — is removed.
      * If no closing fence is found, the body is returned UNCHANGED (a later `---`
        horizontal rule must never be mistaken for a fence, and we never truncate content).
    """
    # Preserve leading blank lines' effect: split on \n, find first non-blank line.
    lines = body.split("\n")
    start = 0
    while start < len(lines) and lines[start].strip() == "":
        start += 1

    # First meaningful line must be exactly the opening fence `---`.
    if start >= len(lines) or lines[start] != "---":
        return body

    # Find the closing fence: the NEXT line that is exactly `---` or `...`.
    close = None
    for i in range(start + 1, len(lines)):
        if lines[i] == "---" or lines[i] == "...":
            close = i
            break

    # No closing fence → conservative: leave the body untouched.
    if close is None:
        return body

    # Drop everything through the closing fence, plus any immediately following blanks.
    rest = close + 1
    while rest < len(lines) and lines[rest].strip() == "":
        rest += 1

    return "\n".join(lines[rest:])


# ── D5 (ADR-0067): entity canonicalisation (no silent fuzzy merge) ───────────────
#
# Legal-suffix token sequences stripped from the END of an entity title, matching the ADR-0067
# D5 list exactly (Inc/Inc., Ltd/Ltd., Corp/Corp., LLC, PLC, LLP, GmbH, S.p.A., "PRIVATE LIMITED").
# Compared token-wise on the punctuation-normalized casefolded title, so "Inc." / "Inc" collapse
# to the token "inc" and "S.p.A." to the token sequence ("s", "p", "a").
_LEGAL_SUFFIX_TOKENS: tuple[tuple[str, ...], ...] = (
    ("private", "limited"),
    ("s", "p", "a"),
    ("inc",),
    ("ltd",),
    ("corp",),
    ("llc",),
    ("plc",),
    ("llp",),
    ("gmbh",),
)

# SMALL, conservative acronym → longform fold. EXACT keys only — never a fuzzy / embedding fold
# (those are a later Review-queue retrofit — ADR-0067 D5). A longform not in this map maps to
# itself, so acronym and longform resolve to the same canonical key.
_ACRONYM_FOLD: dict[str, str] = {
    "aws": "amazon web services",
    "gcp": "google cloud platform",
    "azure": "microsoft azure",
}

# Non-word / non-space run → single space (unicode-aware: keeps accented letters, drops
# punctuation). Applied after parenthetical stripping when computing a canonical entity key.
_CANON_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_CANON_PARENS_RE = re.compile(r"\([^)]*\)")


def _resolve_canonical_entity_key(title: str) -> str:
    """
    Normalized identity key for an entity title (ADR-0067 D5) — pure + deterministic.

    Steps: casefold → strip parenthetical acronyms ``(AWS)`` → punctuation → single spaces →
    strip trailing legal suffixes (``Inc./Ltd./Corp./LLC/PLC/LLP/GmbH/S.p.A./PRIVATE LIMITED``) →
    apply the small conservative acronym↔longform fold. Used to detect an EXACT-key match with an
    existing live entity page BEFORE slugging, so ``Amazon Web Services (AWS)`` / ``AWS`` /
    ``amazon web services inc.`` collapse to one page. NEVER a fuzzy/embedding merge — that is a
    Review-queue retrofit (TODO: ops/dedup_entities.py). Two genuinely different entities
    (``Deloitte`` vs ``Deloitte Italia``) MUST NOT collide.
    """
    s = (title or "").casefold()
    s = _CANON_PARENS_RE.sub(" ", s)
    s = _CANON_PUNCT_RE.sub(" ", s)
    tokens = s.split()
    # Strip trailing legal suffixes (bounded loop; never strips the entire name away).
    changed = True
    while changed and tokens:
        changed = False
        for suffix in _LEGAL_SUFFIX_TOKENS:
            n = len(suffix)
            if len(tokens) > n and tuple(tokens[-n:]) == suffix:
                tokens = tokens[:-n]
                changed = True
                break
    key = " ".join(tokens)
    return _ACRONYM_FOLD.get(key, key)


async def _find_canonical_entity_page(title: str, *, exclude_rel_path: str) -> Page | None:
    """
    Find a LIVE entity page whose canonical key (``_resolve_canonical_entity_key``) EXACTLY
    matches *title*'s and whose file_path differs from *exclude_rel_path* (the naive-slug target,
    which the caller's own existing-row lookup already handles). Returns None when there is no
    cross-slug canonical match (ADR-0067 D5).

    Indexed query over live entity pages only (I1 — no vault re-scan), bounded to ``type=entity``.
    EXACT-key match only; the caller reuses the returned page's id + file_path (merge, not mint).
    """
    from sqlalchemy import select

    key = _resolve_canonical_entity_key(title)
    if not key:
        return None
    match: Page | None = None
    async with orch.get_session() as sess:
        rows = (
            (
                await sess.execute(
                    select(Page).where(
                        Page.vault_id == settings.vault_id,
                        Page.page_type == PageType.ENTITY.value,
                        Page.deleted_at.is_(None),
                        Page.title.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            if row.file_path == exclude_rel_path:
                continue  # the naive-slug target itself → existing-row lookup handles id reuse
            if _resolve_canonical_entity_key(row.title or "") == key:
                match = row
                break
        if match is not None:
            sess.expunge(match)
    return match


async def _resolve_related_slugs(body: str, *, exclude_rel_path: str, cap: int = 8) -> list[str]:
    """
    Resolve *body*'s outbound ``[[wikilinks]]`` to the SLUGS of live pages they point to
    (ADR-0067 D2 — the ``related:`` frontmatter seed). Only RESOLVABLE slugs are returned (a
    target that does not map to a live page is dropped — never a ghost slug), self-links are
    excluded, order is preserved, deduped, capped at *cap*.

    Resolution precedence mirrors K5 ``persist_links``/``_resolve_target`` (exact title →
    case-insensitive → slug-of-title), so the ``related`` seed and the wikilink edges agree. The
    emitted slug is the target page's on-disk file stem (authoritative — handles source-identity
    stems). One indexed query over live pages (I1 — no N+1, no re-scan).
    """
    from sqlalchemy import select

    from app.wiki.links import parse_wikilinks

    parsed = parse_wikilinks(body)
    if not parsed:
        return []
    async with orch.get_session() as sess:
        rows = (
            await sess.execute(
                select(Page.title, Page.file_path).where(
                    Page.vault_id == settings.vault_id,
                    Page.deleted_at.is_(None),
                    Page.title.is_not(None),
                )
            )
        ).all()

    by_title: dict[str, str] = {}
    by_lower: dict[str, str] = {}
    by_slug: dict[str, str] = {}
    for row in rows:
        title = row.title
        file_path = row.file_path
        if not title or not file_path:
            continue
        slug = Path(file_path).stem
        by_title.setdefault(title, slug)
        by_lower.setdefault(title.lower(), slug)
        by_slug.setdefault(orch._slugify(title), slug)

    exclude_slug = Path(exclude_rel_path).stem
    out: list[str] = []
    seen: set[str] = set()
    for pl in parsed:
        target = pl.target
        resolved = (
            by_title.get(target)
            or by_lower.get(target.lower())
            or by_slug.get(orch._slugify(target))
        )
        if not resolved or resolved == exclude_slug or resolved in seen:
            continue
        seen.add(resolved)
        out.append(resolved)
        if len(out) >= cap:
            break
    return out


async def write_wiki_page(
    session: object | None,
    page: WikiPage,
    origin_source: str,
    *,
    provider: InferenceProvider | None = None,
    resolver_maps: Any | None = None,
    skip_index_update: bool = False,
    skip_version_bump: bool = False,
) -> Page:
    """
    Serialize *page* to vault/wiki/<type-plural>/<slug>.md with valid frontmatter (I5) and
    persist it incrementally via the v0.1 primitives (I1): persist_metadata → upsert_vector →
    append_log → bump_version. Returns the persisted `Page` ORM row.

    This is the SINGLE write path shared by the orchestrated loop and (via the MCP server's
    write_page tool, ADR-0010 §2) the CLI delegated path — import-clean so the MCP server
    reuses it directly. The frontmatter block is rebuilt from the typed WikiFrontmatter so the
    body and metadata are serialized exactly once (ADR-0011 — content excludes frontmatter).

    `session` is accepted for the MCP-tool call convention (the tool may hold a session); the
    underlying primitives manage their own sessions, so it may be None. The returned Page is
    re-loaded post-commit so the caller gets the live row.

    BE-PERF-2 per-document coalescing (all optional, default-off — every existing single-page
    call site is unaffected):
      * ``resolver_maps`` — an ``app.wiki.links._ResolverMaps`` built once per document by the
        caller (``build_resolver_maps``) and kept current via ``add_page_to_resolver_maps``. When
        given, this call updates it with the page just written and passes it straight into
        ``persist_links`` instead of re-querying Postgres for every page in the document.
      * ``skip_index_update`` — when True, do NOT regenerate ``index.md`` here; the caller will
        call ``update_index`` itself once after the whole document's pages are written.
      * ``skip_version_bump`` — when True, do NOT bump ``data_version`` here; the caller bumps
        once after the whole document (or block batch) completes.
    """
    from sqlalchemy import select

    page_type = page.type.value
    subdir = type_subdir(page.type)
    slug = orch._slugify(page.title)
    generation_key = page.frontmatter.synapse_generation_key
    # ADR-0074: corpus-derived pages are addressed by their stable member-signature, not by a
    # provider-authored title. A force run may legitimately rename the page; the key therefore
    # owns both the DB identity and a deterministic path that remains unchanged across reruns.
    if generation_key is not None:
        digest = generation_key.rsplit(":", 1)[1]
        slug = f"{page_type}-{digest[:20]}"
    # D3 (ADR-0063 §9, nashsu/llm_wiki parity — source-identity.ts:39-48): a SOURCE page lands at
    # wiki/sources/<stem>.md, where <stem> is the origin source's identity stem (the raw filename),
    # NOT the title slug — so one raw file maps deterministically to one source page (`Source:
    # <identity>` titles otherwise slugify to `source-...`). Falls back to the title slug when the
    # origin carries no `raw/sources/` identity (model-authored source pages, MCP writes stay
    # title-driven — documented caveat).
    if page.type is PageType.SOURCE:
        _identity_stem = _source_identity_stem(origin_source)
        if _identity_stem:
            slug = orch._slugify(_identity_stem)
    rel_path = f"wiki/{subdir}/{slug}.md"

    # D5 (ADR-0067): entity canonicalisation. For type=entity ONLY, BEFORE committing to the
    # naive-slug path, look up an existing LIVE entity whose CANONICAL key matches and redirect
    # this write to it (reuse id + file_path; union sources + merge body via the seams below).
    # EXACT-key match only — a fuzzy/embedding merge is a later Review-queue retrofit (never a
    # silent merge; e.g. "Deloitte" vs "Deloitte Italia" stay distinct). Collapses
    # "Amazon Web Services (AWS)" / "AWS" / "amazon web services inc." into one page.
    if page.type is PageType.ENTITY:
        _canonical = await _find_canonical_entity_page(page.title, exclude_rel_path=rel_path)
        if _canonical is not None and _canonical.file_path:
            logger.info(
                "write_wiki_page: entity canonical merge — %r → existing %s (id=%s) [ADR-0067 D5]",
                page.title,
                _canonical.file_path,
                _canonical.id,
            )
            rel_path = _canonical.file_path
    abs_path = settings.vault_root / rel_path

    # Reuse the existing LIVE page's id when this slug already exists — e.g. the same entity is
    # (re-)generated from a second source, or the same source is re-ingested. persist_metadata
    # keys on page.id, so a fresh uuid4() would always take the INSERT branch and violate the
    # (vault_id, file_path) "_live" unique constraint. Mirrors the watcher/file path which reuses
    # existing.id. deleted_at IS NULL → only adopt a live row's id (a soft-deleted same-path row
    # does not collide with the partial _live index; it resurrects only on the file-ingest path).
    async with orch.get_session() as _id_sess:
        existing_page = None
        if generation_key is not None:
            existing_page = (
                await _id_sess.execute(
                    select(Page).where(
                        Page.vault_id == settings.vault_id,
                        Page.generation_key == generation_key,
                        Page.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if existing_page is not None:
                rel_path = existing_page.file_path
                abs_path = settings.vault_root / rel_path
        if existing_page is None:
            existing_page = (
                await _id_sess.execute(
                    select(Page).where(
                        Page.vault_id == settings.vault_id,
                        Page.file_path == rel_path,
                        Page.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
    page_id = existing_page.id if existing_page is not None else uuid.uuid4()

    sources = list(page.frontmatter.sources)
    if origin_source and origin_source not in sources:
        sources.append(origin_source)
    # Preserve provenance across re-generation: union with the prior row's sources so a page
    # supported by multiple sources keeps all of them (drives F13 shared-entity detection, and
    # avoids silently dropping sources on the UPDATE branch of persist_metadata).
    if existing_page is not None and existing_page.sources:
        for _prior_source in existing_page.sources:
            if _prior_source not in sources:
                sources.append(_prior_source)

    # Build the .md file: frontmatter block + body (ADR-0011).
    # DEFENSIVE: strip a stray leading frontmatter block from the body before composing, so a
    # provider that violated the "content is body-only" contract (e.g. the CLI agent passing a
    # `content` that already begins with `---\n...\n---`) does not produce a DUPLICATED
    # frontmatter block. Applies to BOTH the orchestrated loop and the MCP/CLI write path since
    # this is the single shared write seam (ADR-0010 §2). All downstream uses (file bytes, hash,
    # Qdrant text, wikilink parse) use `body` so nothing desyncs.
    body = _strip_leading_frontmatter(page.content)
    # ── Feature 2 (ADR-0063 §4): LLM body-merge on re-ingest ─────────────────────
    # When this page targets an EXISTING file with meaningful prior body content, merge old+new
    # bodies via the provider (chat seam, I6) instead of overwriting — so a second source enriching
    # an existing entity/concept page does not silently lose the first source's contribution.
    # Bounded to a single timed provider call; degrade-safe (keeps the new body on any failure).
    # Only the orchestrated write site passes `provider`; meta/catalogue + MCP/CLI callers pass
    # None → no merge (the delegated route runs its own agent loop — ADR-0063 §7 documented gap).
    # llm_wiki parity (isOwnedOnlyBySource → replaceExistingBody): when the existing page's ONLY
    # prior source is the one being re-ingested, a correction/retraction must REPLACE the body —
    # merging would keep stale facts the new version dropped. We only merge when ANOTHER source
    # also contributed (a genuine multi-source enrichment), so no source's content is lost.
    _owned_only_by_origin = _is_owned_only_by_source(
        existing_page.sources if existing_page is not None else None, origin_source
    )
    if provider is not None and abs_path.exists() and not _owned_only_by_origin:
        from app.ingest.page_merge import maybe_merge_page_body
        from app.ops.enrich_wikilinks import _split_frontmatter

        try:
            _existing_body = _split_frontmatter(abs_path.read_text(encoding="utf-8"))[1]
        except OSError:
            _existing_body = ""
        body = await maybe_merge_page_body(
            provider,
            _existing_body,
            body,
            title=page.title,
            origin_source=origin_source,
        )
    # K6 navigation tags (nashsu/llm_wiki parity): the WikiFrontmatter validator already
    # trimmed/lowercased/deduped/capped them. Serialize as an Obsidian-valid YAML list ONLY when
    # non-empty so pages without tags keep a clean, minimal frontmatter block (I5).
    tags = list(page.frontmatter.tags)
    # D2 (ADR-0067): `related` = SLUGS of live pages the (final, possibly-merged) body links to —
    # a second F4 graph-edge seed. Resolvable slugs only (a ghost target is dropped), self
    # excluded, capped at 8. Resolved against currently-live pages BEFORE this page is persisted.
    related = await _resolve_related_slugs(body, exclude_rel_path=rel_path, cap=8)
    # created/updated (nashsu/llm_wiki parity) — date-only, Obsidian-friendly. `created` is
    # preserved across re-generation by reading the prior on-disk file (still the OLD bytes here,
    # since abs_path is overwritten below); `updated` always advances to today.
    _today = datetime.now(UTC).strftime("%Y-%m-%d")
    _created = _today
    if abs_path.exists():
        try:
            _prior_created = frontmatter.load(str(abs_path)).metadata.get("created")
            if _prior_created:
                _created = str(_prior_created)
        except Exception as _created_exc:  # noqa: BLE001 — best-effort; fall back to today
            logger.debug(
                "write_wiki_page: could not read prior 'created' for %s: %s",
                rel_path,
                _created_exc,
            )
    # D2 (ADR-0067): emit frontmatter in LLM Wiki BYTE-SHAPE + key order and DROP `sources`/`lang`
    # from the .md (sort_keys=False). Provenance is preserved in Postgres — pages.sources is
    # written below from `sources` (origin injected), which the graph source-overlap ×4 (F4) +
    # cascade-delete (F13) read. This is EMISSION-ONLY: the WikiFrontmatter object + the DB write
    # still carry sources/lang. Source pages additionally emit bibliographic keys when present.
    _extra = page.frontmatter.model_dump()
    ordered: dict[str, Any] = {
        "type": page_type,  # serialize enum as its string value for Obsidian (I5)
        "title": page.frontmatter.title,
        "created": _created,
        "updated": _today,
    }
    if generation_key is not None:
        ordered["synapse_generation_key"] = generation_key
    if tags:
        ordered["tags"] = tags
    if related:
        ordered["related"] = related
    if page.type is PageType.SOURCE:
        for _bib_key in ("authors", "year", "url", "venue"):
            _bib_val = _extra.get(_bib_key)
            if _bib_val not in (None, "", [], {}):
                ordered[_bib_key] = _bib_val
    post = frontmatter.Post(body, **ordered)
    serialized = frontmatter.dumps(post, sort_keys=False)
    # content_hash MUST hash the exact bytes written to disk (serialized + trailing newline), NOT
    # `serialized` alone — otherwise the stored hash never matches the file and every on-disk hash
    # comparison (GET/PUT /pages/{id}/content optimistic-lock, ADR-0035) sees a spurious mismatch.
    # reindex_wiki_page_body() already hashes the full file bytes; mirror it here.
    file_text = serialized + "\n"
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(file_text, encoding="utf-8")

    await orch.persist_metadata(
        page_id=page_id,
        vault_id=settings.vault_id,
        file_path=rel_path,
        title=page.title,
        page_type=page_type,
        sources=sources,
        tags=tags or None,
        generation_key=generation_key,
        content_hash=orch._sha256(file_text.encode("utf-8")),
        source_mtime_ns=0,
    )
    await orch.upsert_vector(
        page_id=page_id,
        text=body,
        file_path=rel_path,
        title=page.title,
        page_type=page_type,
    )
    # K4: log this indexed file. This seam is the WATCHER indexing a raw wiki page (one file → one
    # page → one log line, correct). The multi-page LLM-generation paths (block_writer / pipeline)
    # instead log only the source page — see their append_log gates.
    await orch.append_log(rel_path, page_type=page_type, title=page.title)
    if not skip_version_bump:
        await orch.bump_version()

    # ── K5: parse + persist wikilinks (incremental, I1) ──────────────────────
    from app.wiki.links import add_page_to_resolver_maps, parse_wikilinks, persist_links

    parsed = parse_wikilinks(body)
    # BE-PERF-2: when the caller shares one resolver-maps object across a whole document's
    # pages, fold this page into it IN MEMORY (no query) before resolving its own links, so
    # forward references within the same document resolve exactly as they would if the maps
    # had been re-queried from Postgres after this page's commit above.
    if resolver_maps is not None:
        add_page_to_resolver_maps(
            resolver_maps, page_id=page_id, title=page.title, file_path=rel_path
        )
    async with orch.get_session() as wl_sess:
        await persist_links(wl_sess, page_id, parsed, maps=resolver_maps)

    # ── K3: regenerate index.md catalogue (idempotent, I1) ───────────────────
    if not skip_index_update:
        from app.wiki.index import update_index

        async with orch.get_session() as idx_sess:
            await update_index(idx_sess, settings.vault_root)

    logger.info("write_wiki_page: wrote %s page_id=%s", rel_path, page_id)

    async with orch.get_session() as sess:
        row = await sess.execute(select(Page).where(Page.id == page_id))
        result = row.scalar_one()
        sess.expunge(result)
        return result


# D3 (ADR-0063 §9, nashsu/llm_wiki parity — source-identity.ts:1-24). The "source identity" is the
# origin path with the leading `raw/sources/` prefix removed (case-insensitive), or — if a
# `raw/sources/` marker appears mid-path — the suffix after it; else the bare filename. It is the
# stable, human-readable label llm_wiki puts in the source-summary title/body and sources[].
_RAW_SOURCES_PREFIX = "raw/sources/"
_RAW_SOURCES_MARKER = "/raw/sources/"


def _source_identity(origin_source: str) -> str:
    """
    Return the nashsu/llm_wiki "source identity" for *origin_source* (D3, source-identity.ts:6-24).

    Normalizes separators to '/', then strips a leading `raw/sources/` (case-insensitive) or the
    suffix after an embedded `/raw/sources/` marker; falls back to the bare filename when neither
    is present. Empty input → "". Used for the synthesized source-summary title/body AND the
    on-disk slug (see _source_identity_stem).
    """
    sp = (origin_source or "").replace("\\", "/").lstrip("/")
    if not sp:
        return ""
    key = sp.lower()
    if key.startswith(_RAW_SOURCES_PREFIX):
        return sp[len(_RAW_SOURCES_PREFIX) :]
    marker = key.find(_RAW_SOURCES_MARKER)
    if marker >= 0:
        return sp[marker + len(_RAW_SOURCES_MARKER) :]
    return Path(sp).name


def _source_identity_stem(origin_source: str) -> str:
    """
    Return the filename stem of the source identity (D3) — the `<stem>` in llm_wiki's
    `wiki/sources/<stem>.md` path (source-identity.ts:39-48). "" when there is no identity so the
    writer can fall back to the title-derived slug (model-authored source pages, MCP writes).
    """
    identity = _source_identity(origin_source)
    return Path(identity).stem if identity else ""
