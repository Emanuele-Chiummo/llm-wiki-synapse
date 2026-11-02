#!/usr/bin/env python3
"""
reindex_wiki_from_vault.py — disaster recovery: rebuild pages + links + Qdrant from wiki/ files.

When the Postgres and Qdrant volumes are lost but the vault survives, every wiki page already
exists on disk as an Obsidian-valid ``.md`` file (I5) carrying its own metadata in the YAML
frontmatter (``type``, ``title``, ``tags``, ``sources``). This script re-indexes those files
WITHOUT any LLM call: no re-ingest of raw sources, no page rewrite, zero inference cost.

What it does, per ``wiki/**/*.md`` file (hidden dirs such as ``.obsidian/`` are skipped):
    1. Parses the frontmatter and body (``parse_page_file``, pure).
    2. Upserts the ``pages`` row via ``persist_metadata`` — the same primitive the live writer
       uses. The id of an existing live row for the same ``file_path`` is reused, so the script
       is idempotent and safe to re-run after an interruption.
    3. Embeds the body into Qdrant via ``upsert_vector`` (bge-m3 on the configured
       ``EMBEDDING_URL``; skipped with ``--no-embed`` or when embeddings are disabled).
Then, once every page row exists (so forward references resolve):
    4. Re-derives the K5 ``links`` rows from each body with ONE shared resolver map
       (``build_resolver_maps`` + ``persist_links``) — the graph edges (F4).
    5. Bumps ``data_version`` ONCE, which makes the GraphCache recompute the layout (I2).

What it deliberately does NOT do:
    - It never writes to the vault: no frontmatter rewrite, no ``index.md`` regeneration, no
      ``log.md`` entries (calling ``write_wiki_page`` would append 6k lines to ``log.md``).
    - It never deletes rows: pages in the DB whose file is gone are only counted.

``sources`` provenance: the frontmatter carries the source identity (path relative to
``raw/sources/``); the live writer additionally stamps the vault-relative origin
(``raw/sources/<identity>``). Both forms are restored when the raw file still exists, so the
source → derived-pages view and cascade delete (F13) keep matching.

I1 note: this is a full vault walk BY DESIGN — it is an explicit, operator-run recovery tool,
never wired to startup, the watcher or a schedule. The live system stays incremental.

Usage (inside the backend container, where the app settings come from the environment):
    python scripts/reindex_wiki_from_vault.py --dry-run
    python scripts/reindex_wiki_from_vault.py --max-pages 20      # trial run
    python scripts/reindex_wiki_from_vault.py                     # full run
    python scripts/reindex_wiki_from_vault.py --no-embed          # Postgres + links only
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import frontmatter  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import get_session  # noqa: E402
from app.ingest.orchestrator import bump_version, persist_metadata, upsert_vector  # noqa: E402
from app.models import Link, Page  # noqa: E402
from app.wiki.links import build_resolver_maps, parse_wikilinks, persist_links  # noqa: E402
from app.wiki.summary import extract_first_paragraph_summary  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

# Folder → page type, used only when a file has no (or an unreadable) ``type`` key.
_DIR_TYPE: dict[str, str] = {
    "entities": "entity",
    "concepts": "concept",
    "sources": "source",
    "synthesis": "synthesis",
    "comparisons": "comparison",
    "queries": "query",
}
_ROOT_TYPE: dict[str, str] = {"overview.md": "overview", "index.md": "index", "log.md": "log"}

LINK_BATCH_SIZE = 200


@dataclass
class PageRecord:
    """Everything needed to index one wiki file, derived from disk alone."""

    rel_path: str
    title: str
    page_type: str | None
    tags: list[str] | None
    sources: list[str] | None
    generation_key: str | None
    body: str
    summary: str | None
    content_hash: str


@dataclass
class ReindexSummary:
    files_found: int = 0
    pages_written: int = 0
    pages_reused_id: int = 0
    vectors_attempted: int = 0
    links_written: int = 0
    links_dangling: int = 0
    stale_db_pages: int = 0
    errors: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0


def collect_wiki_files(wiki_dir: Path) -> list[Path]:
    """All ``*.md`` under *wiki_dir*, sorted, skipping hidden files and dirs (``.obsidian``)."""
    found: list[Path] = []
    for path in wiki_dir.rglob("*.md"):
        rel_parts = path.relative_to(wiki_dir).parts
        if any(part.startswith(".") for part in rel_parts):
            continue
        if path.is_file():
            found.append(path)
    return sorted(found)


def _as_str_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if v is not None and str(v).strip()]
    return [str(value).strip()]


def _fallback_type(rel_path: str) -> str | None:
    parts = Path(rel_path).parts  # ("wiki", "concepts", "foo.md") or ("wiki", "index.md")
    if len(parts) == 2:
        return _ROOT_TYPE.get(parts[1])
    return _DIR_TYPE.get(parts[1]) if len(parts) > 2 else None


def normalize_sources(identities: list[str], raw_sources_dir: Path) -> list[str] | None:
    """
    Frontmatter identities plus their ``raw/sources/<identity>`` form when that raw file exists,
    mirroring what the live writer stored (identity from frontmatter + origin path).
    """
    out: list[str] = []
    for ident in identities:
        if ident not in out:
            out.append(ident)
        if ident.lower().startswith("raw/"):
            continue
        prefixed = f"raw/sources/{ident}"
        if prefixed not in out and (raw_sources_dir / ident).is_file():
            out.append(prefixed)
    return out or None


def parse_page_file(vault_root: Path, abs_path: Path) -> PageRecord:
    """Build a PageRecord from one wiki file. Pure apart from reading the file."""
    rel_path = abs_path.relative_to(vault_root).as_posix()
    raw_bytes = abs_path.read_bytes()
    text = raw_bytes.decode("utf-8", errors="replace")
    try:
        post = frontmatter.loads(text)
        meta: dict[str, Any] = dict(post.metadata)
        body = post.content
    except Exception:  # noqa: BLE001 — malformed YAML: index the whole text, metadata from path
        meta, body = {}, text

    title = str(meta.get("title") or "").strip() or abs_path.stem
    page_type = str(meta.get("type") or "").strip() or _fallback_type(rel_path)
    tags = [t.lower() for t in _as_str_list(meta.get("tags"))] or None
    sources = normalize_sources(_as_str_list(meta.get("sources")), vault_root / "raw" / "sources")
    gen_key = meta.get("synapse_generation_key")

    return PageRecord(
        rel_path=rel_path,
        title=title,
        page_type=page_type,
        tags=tags,
        sources=sources,
        generation_key=str(gen_key) if gen_key else None,
        body=body,
        summary=extract_first_paragraph_summary(body),
        content_hash=hashlib.sha256(raw_bytes).hexdigest(),
    )


async def _load_live_ids(vault_id: str) -> dict[str, uuid.UUID]:
    async with get_session() as session:
        rows = await session.execute(
            select(Page.id, Page.file_path).where(
                Page.vault_id == vault_id, Page.deleted_at.is_(None)
            )
        )
        return {fp: pid for pid, fp in rows.all()}


async def reindex_vault(
    *,
    vault_root: Path,
    vault_id: str,
    dry_run: bool = False,
    max_pages: int = 0,
    embed: bool = True,
    concurrency: int = 4,
    log: Any = print,
) -> ReindexSummary:
    started = time.monotonic()
    summary = ReindexSummary()
    files = collect_wiki_files(vault_root / "wiki")
    summary.files_found = len(files)
    if max_pages:
        files = files[:max_pages]

    records: list[PageRecord] = []
    for path in files:
        try:
            records.append(parse_page_file(vault_root, path))
        except Exception as exc:  # noqa: BLE001 — one unreadable file must not stop recovery
            summary.errors.append(f"parse {path}: {exc}")

    live_ids = await _load_live_ids(vault_id)
    on_disk = {r.rel_path for r in records}
    if not max_pages:
        summary.stale_db_pages = sum(
            1 for fp in live_ids if fp.startswith("wiki/") and fp not in on_disk
        )

    log(
        f"[reindex] vault_id={vault_id} files={summary.files_found} to_index={len(records)} "
        f"live_rows={len(live_ids)} embed={embed} dry_run={dry_run}"
    )
    if dry_run:
        for r in records[:10]:
            log(f"[reindex] would index {r.rel_path} type={r.page_type} title={r.title!r}")
        summary.elapsed_s = time.monotonic() - started
        return summary

    # ── Pass 1: page rows + vectors ───────────────────────────────────────────
    ids: dict[str, uuid.UUID] = {}
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _embed(rec: PageRecord, page_id: uuid.UUID) -> None:
        async with sem:
            try:
                await upsert_vector(
                    page_id=page_id,
                    text=rec.body,
                    file_path=rec.rel_path,
                    title=rec.title,
                    page_type=rec.page_type,
                    vault_id=vault_id,
                )
            except Exception as exc:  # noqa: BLE001 — Qdrant failure: page stays lexical-only
                summary.errors.append(f"vector {rec.rel_path}: {exc}")

    pending: list[asyncio.Task[None]] = []
    for i, rec in enumerate(records, 1):
        page_id = live_ids.get(rec.rel_path)
        if page_id is not None:
            summary.pages_reused_id += 1
        else:
            page_id = uuid.uuid4()
        try:
            await persist_metadata(
                page_id=page_id,
                vault_id=vault_id,
                file_path=rec.rel_path,
                title=rec.title,
                page_type=rec.page_type,
                sources=rec.sources,
                tags=rec.tags,
                generation_key=rec.generation_key,
                summary=rec.summary,
                content_hash=rec.content_hash,
                source_mtime_ns=0,
            )
        except Exception as exc:  # noqa: BLE001
            summary.errors.append(f"page {rec.rel_path}: {exc}")
            continue
        ids[rec.rel_path] = page_id
        summary.pages_written += 1
        if embed:
            summary.vectors_attempted += 1
            pending.append(asyncio.create_task(_embed(rec, page_id)))
            if len(pending) >= 4 * max(1, concurrency):
                await asyncio.gather(*pending)
                pending.clear()
        if i % 250 == 0:
            log(f"[reindex] pages {i}/{len(records)}")
    if pending:
        await asyncio.gather(*pending)

    # ── Pass 2: wikilinks, resolved against the complete page set ─────────────
    async with get_session() as session:
        maps = await build_resolver_maps(session, vault_id)
    indexed = [r for r in records if r.rel_path in ids]
    for start in range(0, len(indexed), LINK_BATCH_SIZE):
        batch = indexed[start : start + LINK_BATCH_SIZE]
        try:
            async with get_session() as session:
                for rec in batch:
                    parsed = parse_wikilinks(rec.body)
                    await persist_links(session, ids[rec.rel_path], parsed, maps=maps)
                    summary.links_written += len(parsed)
        except Exception as exc:  # noqa: BLE001
            summary.errors.append(f"links batch at {start}: {exc}")
        log(f"[reindex] links {min(start + LINK_BATCH_SIZE, len(indexed))}/{len(indexed)}")

    async with get_session() as session:
        summary.links_dangling = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(Link)
                    .join(Page, Link.source_page_id == Page.id)
                    .where(Link.dangling.is_(True), Page.vault_id == vault_id)
                )
            ).scalar_one()
        )

    if summary.pages_written:
        await bump_version()

    summary.elapsed_s = time.monotonic() - started
    return summary


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Rebuild pages, links and vectors from wiki/ files.")
    p.add_argument("--dry-run", action="store_true", help="Parse and report, write nothing.")
    p.add_argument("--max-pages", type=int, default=0, help="Index only the first N files.")
    p.add_argument("--no-embed", action="store_true", help="Skip the Qdrant re-embedding.")
    p.add_argument("--concurrency", type=int, default=4, help="Parallel embedding calls.")
    return p.parse_args(argv)


async def _main(args: argparse.Namespace) -> int:
    summary = await reindex_vault(
        vault_root=settings.vault_root,
        vault_id=settings.vault_id,
        dry_run=args.dry_run,
        max_pages=args.max_pages,
        embed=not args.no_embed,
        concurrency=args.concurrency,
    )
    print(
        f"[reindex] done in {summary.elapsed_s:.0f}s: files={summary.files_found} "
        f"pages={summary.pages_written} (reused_id={summary.pages_reused_id}) "
        f"vectors={summary.vectors_attempted} links={summary.links_written} "
        f"dangling={summary.links_dangling} stale_db_pages={summary.stale_db_pages} "
        f"errors={len(summary.errors)}"
    )
    for err in summary.errors[:20]:
        print(f"[reindex] ERROR {err}")
    return 1 if summary.errors else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(_parse_args())))
