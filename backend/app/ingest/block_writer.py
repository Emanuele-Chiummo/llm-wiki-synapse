"""Raw FILE-block writer for the block-based orchestrated ingest path (ADR-0076).

The block pipeline (nashsu/llm_wiki v0.6.3 parity) emits pages whose frontmatter ``type`` may be
a CUSTOM schema-defined type (``thesis``, ``goal``, ``habit``, …). ``WikiPage.type`` is a strict
:class:`app.ingest.schemas.PageType` enum and CANNOT represent those, so the block path must NOT
go through :func:`app.ingest.writer.write_wiki_page`. Instead this module writes the raw sanitized
FILE-block body verbatim and persists a ``pages`` row with ``page_type`` set to the RAW type
string (``pages.type`` is a nullable ``str`` column, so a custom type round-trips).

Persistence reuses the SAME low-level primitives ``write_wiki_page`` uses, reached via
``orch.<name>`` so monkeypatched tests still resolve through ``app.ingest.orchestrator``:
``persist_metadata`` (metadata row, ``page_type`` = raw type) → ``upsert_vector`` (embed the
frontmatter-stripped body) → ``append_log`` (K4) → ``bump_version`` (F16 dataVersion) plus
``app.wiki.links.parse_wikilinks`` / ``persist_links`` (K5) and ``app.wiki.index.update_index``
(K3). Behaviour mirrors llm_wiki ``writeFileBlocks`` (ingest.ts:1783-1956): drop app-managed
aggregates (``wiki/index.md`` / ``wiki/overview.md``), reject unsafe paths, validate schema
routing (drop on mismatch), guarantee the active source identity is present in ``sources[]``, and
back up the prior bytes to ``page-history`` before an overwrite.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

import frontmatter

import app.ingest.orchestrator as orch
from app.config import settings
from app.ingest.blocks import is_safe_ingest_path
from app.ingest.provider.base import InferenceProvider
from app.models import Page
from app.wiki.schema import validate_page_routing
from app.wiki.summary import extract_first_paragraph_summary

logger = logging.getLogger(__name__)

__all__ = ["write_block_page"]

# App-managed aggregate pages the model must never write (llm_wiki writeFileBlocks step 2 —
# ingest.ts:1793). Compared case-insensitively on the wiki-relative path.
# log.md is included: it is code-appended (append_log, K4) with one "## [date] ingest | <source>"
# entry per ingest. Letting the model emit a log.md block overwrote that file (destroying its
# frontmatter and mixing a second, schema-described format into it) — a real parity regression.
_APP_MANAGED_AGGREGATES: frozenset[str] = frozenset(
    {"wiki/index.md", "wiki/overview.md", "wiki/log.md"}
)


async def write_block_page(
    *,
    rel_path: str,
    content: str,
    origin_source: str,
    routing: dict[str, str],
    provider: InferenceProvider | None = None,
    resolver_maps: Any | None = None,
    skip_index_update: bool = False,
    skip_version_bump: bool = False,
) -> Page | None:
    """Write ONE sanitized FILE block to ``<vault>/rel_path`` and index it (ADR-0076).

    *content* is the ALREADY-sanitized file body (the caller runs
    :func:`app.ingest.sanitize.sanitize_ingested_file_content`), so it starts with a ``---``
    frontmatter block. *routing* is the parsed ``## Page Types`` map
    (:func:`app.wiki.schema.parse_page_type_routing`).

    Returns the persisted :class:`~app.models.Page` row, or ``None`` when the block is DROPPED
    (llm_wiki parity): an app-managed aggregate, an unsafe path, or a schema-routing mismatch.
    ``provider`` is accepted for signature parity with the JSON writer but is unused here (the
    block path never LLM-merges — it writes the model's block verbatim).

    BE-PERF-2 per-document coalescing (all optional, default-off — a standalone call, e.g. in
    tests, behaves exactly as before):
      * ``resolver_maps`` — an ``app.wiki.links._ResolverMaps`` built once per document by the
        caller (``build_resolver_maps``) and kept current via ``add_page_to_resolver_maps``.
        When given, folds this page in and reuses it for ``persist_links`` instead of a fresh
        bulk query over every live page.
      * ``skip_index_update`` — when True, skip the ``index.md`` regeneration here; the caller
        runs ``update_index`` once after the whole document's FILE blocks are written.
      * ``skip_version_bump`` — when True, skip the ``data_version`` bump here; the caller bumps
        once after the whole document (or block batch) completes.
    """
    del provider  # signature parity with write_wiki_page; the block path writes verbatim.

    normalized = rel_path.replace("\\", "/").lstrip("/")

    # 1. Drop app-managed aggregates (index.md / overview.md) — maintained separately (K3/F3).
    if normalized.lower() in _APP_MANAGED_AGGREGATES:
        logger.warning("write_block_page: dropping app-managed aggregate %r", rel_path)
        return None

    # 2. Path safety: must be under wiki/, no .., no absolute/drive, Windows-safe (blocks.py).
    if not is_safe_ingest_path(normalized):
        logger.warning("write_block_page: dropping unsafe FILE path %r", rel_path)
        return None

    # 3. Parse frontmatter (reuse orch._parse_frontmatter for the metadata; frontmatter.loads for
    #    the body). The raw ``type`` string is preserved (custom types are legal here).
    meta = orch._parse_frontmatter(content.encode("utf-8"), normalized)
    page_type = str(meta.get("type") or "").strip()
    _title_val = meta.get("title")
    title = str(_title_val).strip() if _title_val is not None else None
    model_sources = _as_str_list(meta.get("sources"))
    tags = _as_str_list(meta.get("tags")) or None
    try:
        body_for_embedding = frontmatter.loads(content).content
    except Exception:  # noqa: BLE001 — malformed FM: fall back to the whole content for embedding
        body_for_embedding = content

    # 4. Schema-routing validation (llm_wiki writeFileBlocks step 8) — drop on mismatch.
    ok, reason = validate_page_routing(page_type, normalized, routing)
    if not ok:
        logger.warning(
            "write_block_page: dropping mis-routed page %r (type=%r): %s",
            rel_path,
            page_type,
            reason,
        )
        return None

    abs_path = settings.vault_root / normalized

    # 5/6. Reuse an existing live row's id (idempotent re-ingest) + union prior sources so a
    #      re-ingested page never silently loses a co-source (mirrors write_wiki_page).
    existing = await _load_existing_block_page(normalized)
    page_id = existing.id if existing is not None else uuid.uuid4()
    prior_sources = existing.sources if existing is not None else None
    sources = _merge_sources(model_sources, origin_source, prior_sources)

    # 7. Page-history backup BEFORE overwriting the prior bytes (llm_wiki page-history parity).
    if abs_path.exists():
        _backup_page_history(normalized, abs_path)

    # 8. Write the block verbatim (atomic temp-file + replace — crash-safe). content_hash MUST
    #    hash the exact bytes written so the GET /pages content optimistic-lock never mismatches.
    file_text = content if content.endswith("\n") else content + "\n"
    new_bytes = file_text.encode("utf-8")
    _atomic_write(abs_path, new_bytes)

    # 9. Persist via the shared low-level primitives (page_type = RAW type string — custom types
    #    round-trip through the nullable pages.type column).
    await orch.persist_metadata(
        page_id=page_id,
        vault_id=settings.vault_id,
        file_path=normalized,
        title=title,
        page_type=page_type or None,
        sources=sources or None,
        tags=tags,
        summary=extract_first_paragraph_summary(body_for_embedding),
        content_hash=orch._sha256(new_bytes),
        source_mtime_ns=0,
    )
    await orch.upsert_vector(
        page_id=page_id,
        text=body_for_embedding,
        file_path=normalized,
        title=title,
        page_type=page_type or None,
        vault_id=settings.vault_id,
    )
    # K4: ONE log line per ingest — only the source page. llm_wiki appends
    # "## [date] ingest | <source title>" once per source, not once per generated page.
    if page_type == "source":
        await orch.append_log(normalized, title=title)
    if not skip_version_bump:
        await orch.bump_version()

    # K5: parse + persist wikilinks from the body (incremental, I1 — F4 direct-link ×3 edges).
    from app.wiki.links import add_page_to_resolver_maps, parse_wikilinks, persist_links

    parsed = parse_wikilinks(body_for_embedding)
    # BE-PERF-2: fold this page into the caller's shared resolver maps (if any) IN MEMORY —
    # no query — before resolving its own links (see write_wiki_page for the full rationale).
    if resolver_maps is not None:
        add_page_to_resolver_maps(resolver_maps, page_id=page_id, title=title, file_path=normalized)
    async with orch.get_session() as wl_sess:
        await persist_links(wl_sess, page_id, parsed, maps=resolver_maps)

    # K3: regenerate the index.md catalogue (idempotent, I1).
    if not skip_index_update:
        from app.wiki.index import update_index

        async with orch.get_session() as idx_sess:
            await update_index(idx_sess, settings.vault_root)

    logger.info("write_block_page: wrote %s page_id=%s type=%r", normalized, page_id, page_type)

    from sqlalchemy import select

    async with orch.get_session() as sess:
        row = await sess.execute(select(Page).where(Page.id == page_id))
        result = row.scalar_one()
        sess.expunge(result)
        return result


# ── Helpers ──────────────────────────────────────────────────────────────────────


def _as_str_list(value: object) -> list[str]:
    """Coerce a frontmatter value into a clean ``list[str]`` (scalar → one item; else [])."""
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if value is None:
        return []
    text = str(value).strip()
    return [text] if text else []


def _merge_sources(
    model_sources: list[str], origin_source: str, prior_sources: list[str] | None
) -> list[str]:
    """Union model + origin + prior sources, GUARANTEEING *origin_source* is present, deduped
    case-insensitively (llm_wiki ``canonicalizeSourcesField`` intent — ingest.ts:1539-1567).

    Order: model sources first, then the active origin identity (appended iff missing), then any
    prior sources on the existing row (so a re-ingest keeps every co-source).
    """
    out: list[str] = []
    seen: set[str] = set()
    origin_group = [origin_source] if origin_source else []
    for group in (model_sources, origin_group, prior_sources or []):
        for raw in group:
            candidate = str(raw).strip()
            if not candidate:
                continue
            key = candidate.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(candidate)
    return out


async def _load_existing_block_page(normalized_rel: str) -> Page | None:
    """Return the LIVE ``pages`` row for *normalized_rel* (id reuse), or None if absent/deleted."""
    from sqlalchemy import select

    async with orch.get_session() as sess:
        row = await sess.execute(
            select(Page).where(
                Page.vault_id == settings.vault_id,
                Page.file_path == normalized_rel,
                Page.deleted_at.is_(None),
            )
        )
        page = row.scalar_one_or_none()
        if page is not None:
            sess.expunge(page)
        return page


def _atomic_write(abs_path: Path, data: bytes) -> None:
    """Write *data* to *abs_path* atomically (temp file + ``os.replace`` — no partial file)."""
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(abs_path.parent), suffix=".block_tmp")
    try:
        os.write(tmp_fd, data)
        os.close(tmp_fd)
        Path(tmp_name).replace(abs_path)
    except Exception:
        try:
            os.close(tmp_fd)
        except OSError:
            pass
        Path(tmp_name).unlink(missing_ok=True)
        raise


_BACKUP_INDEX_RE = re.compile(r"-(\d+)\.md$")


def _sanitize_backup_stem(normalized_rel: str) -> str:
    """Encode a wiki-relative path as a single filesystem-safe stem (``wiki/thesis/x.md`` →
    ``wiki__thesis__x``). Path separators collapse to ``__``; other unsafe chars → ``_``."""
    stem = normalized_rel[:-3] if normalized_rel.lower().endswith(".md") else normalized_rel
    stem = stem.replace("/", "__").replace("\\", "__")
    return re.sub(r"[^A-Za-z0-9._-]", "_", stem)


def _backup_page_history(normalized_rel: str, abs_path: Path) -> None:
    """Copy the prior bytes at *abs_path* into ``<vault>/.synapse/page-history/`` before an
    overwrite, keeping at most ``settings.ingest_page_history_max_per_page`` backups per page
    (oldest pruned). The index suffix is a DETERMINISTIC monotonic integer (high-water of the
    existing backups + 1), NOT a timestamp — so re-runs are reproducible. Best-effort: any I/O
    error is swallowed (a backup failure must never fail the write)."""
    try:
        history_dir = settings.vault_root / ".synapse" / "page-history"
        history_dir.mkdir(parents=True, exist_ok=True)
        stem = _sanitize_backup_stem(normalized_rel)

        existing = _existing_backups(history_dir, stem)
        next_idx = (existing[-1][0] + 1) if existing else 0
        shutil.copy2(abs_path, history_dir / f"{stem}-{next_idx}.md")

        cap = max(0, int(settings.ingest_page_history_max_per_page))
        backups = _existing_backups(history_dir, stem)
        while len(backups) > cap:
            _idx, oldest = backups.pop(0)
            oldest.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001 — page-history is best-effort; never fail the write.
        logger.debug(
            "write_block_page: page-history backup skipped for %r: %s", normalized_rel, exc
        )


def _existing_backups(history_dir: Path, stem: str) -> list[tuple[int, Path]]:
    """Return ``(index, path)`` for every ``<stem>-<n>.md`` backup, sorted ascending by index."""
    found: list[tuple[int, Path]] = []
    for path in history_dir.glob(f"{stem}-*.md"):
        match = _BACKUP_INDEX_RE.search(path.name)
        if match is not None:
            found.append((int(match.group(1)), path))
    found.sort(key=lambda item: item[0])
    return found
