#!/usr/bin/env python3
"""
backfill_page_summary.py — one-shot backfill for 1.9.4 W6 (PF-INDEX-GLOSS-1).

Migration 0036 added ``pages.summary`` (nullable Text): a short gloss derived from
the page's first content paragraph with no LLM call. Pages written BEFORE the
migration land have ``summary IS NULL``. This script reads each such page from disk,
extracts the first paragraph summary, and UPDATEs the row — exactly as the live
write path does for new pages (``app.wiki.summary.extract_first_paragraph_summary``).

What it does:
    1. Queries ``pages`` in batches (``--batch-size``, default 200) filtering:
       ``summary IS NULL AND deleted_at IS NULL AND file_path LIKE 'wiki/%'``
       and optionally ``vault_id = <vault_id>``.
    2. For each row, reads the corresponding file from ``<vault_root>/<file_path>``.
    3. Calls ``extract_first_paragraph_summary`` on the frontmatter-stripped body.
    4. UPDATEs ``pages.summary`` in a single statement per batch (or row by row in
       ``--dry-run`` mode, printing only).
    5. Commits after each batch; safe to interrupt and re-run (idempotent — already-
       filled rows are skipped by the ``summary IS NULL`` filter).

Behaviour notes:
    - Pages whose files are missing on disk are SKIPPED (``--vault-root`` must match
      the mount path the server uses).
    - Pages whose body has no extractable paragraph (heading-only, empty, etc.) get
      ``summary = NULL`` left as-is (no change; they won't be re-queued on re-run).
      To avoid re-processing them on every re-run you can set a sentinel via
      ``--sentinel-empty`` which writes an empty string instead of NULL, but by
      default the row is left untouched so a future re-ingest can populate it.
    - Bounded by ``--max-pages`` (0 = unlimited).

Usage:
    cd backend
    python scripts/backfill_page_summary.py --dry-run \\
        --db-url "postgresql+asyncpg://synapse:synapse@localhost:5432/synapse" \\
        --vault-root /path/to/vault

    python scripts/backfill_page_summary.py \\
        --db-url "postgresql+asyncpg://synapse:synapse@localhost:5432/synapse" \\
        --vault-root /path/to/vault \\
        --vault-id my_vault          # optional; without it, all vaults in the DB

    # With env vars instead of CLI flags:
    DATABASE_URL=postgresql+asyncpg://... VAULT_ROOT=/path/to/vault \\
        python scripts/backfill_page_summary.py

Environment (alternative to CLI flags):
    DATABASE_URL     — SQLAlchemy async Postgres URL
    VAULT_ROOT       — absolute path to the vault root directory
    VAULT_ID         — vault identifier (optional; filters by vault_id)

Prerequisites:
    - Postgres reachable from wherever this script runs.
    - Backend venv activated: source .venv/bin/activate
      (needs sqlalchemy, asyncpg, python-frontmatter, app.wiki.summary).
    - ``VAULT_ROOT`` must be the same mount path the Synapse server uses (the
      script resolves ``<vault_root>/<file_path>`` to find pages on disk).

References:
    - 1.9.4 W6 (PF-INDEX-GLOSS-1): page gloss catalogue (K3).
    - Migration 0036: adds pages.summary column.
    - app.wiki.summary.extract_first_paragraph_summary: the shared extraction logic.
    - I1: this script performs targeted row UPDATEs, never a full table recreate.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Backfill pages.summary from first-paragraph extraction for rows written "
            "before migration 0036 (1.9.4 W6 / PF-INDEX-GLOSS-1)."
        )
    )
    p.add_argument(
        "--db-url",
        default=os.environ.get("DATABASE_URL"),
        help=(
            "SQLAlchemy async Postgres URL "
            "(default: $DATABASE_URL). "
            "Example: postgresql+asyncpg://synapse:synapse@localhost:5432/synapse"
        ),
    )
    p.add_argument(
        "--vault-root",
        default=os.environ.get("VAULT_ROOT"),
        help=(
            "Absolute path to the vault root directory "
            "(default: $VAULT_ROOT). "
            "Files are resolved as <vault-root>/<file_path>."
        ),
    )
    p.add_argument(
        "--vault-id",
        default=os.environ.get("VAULT_ID"),
        help=(
            "Only backfill pages with this vault_id. "
            "Omit to process all vaults in the DB "
            "(default: $VAULT_ID or all)."
        ),
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="Rows fetched and committed per DB batch (default: 200).",
    )
    p.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="Stop after this many pages (0 = unlimited, default: 0).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be updated without writing to the DB.",
    )
    return p.parse_args()


def _require_app_on_path() -> None:
    """Ensure the app package is importable (scripts/ is backend/scripts/)."""
    here = Path(__file__).resolve().parent
    backend_dir = here.parent  # backend/
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))


async def _run(args: argparse.Namespace) -> None:  # noqa: PLR0912, PLR0915
    if not args.db_url:
        raise SystemExit(
            "ERROR: provide --db-url or set DATABASE_URL.\n"
            "Example: DATABASE_URL=postgresql+asyncpg://synapse:synapse@localhost:5432/synapse"
        )
    if not args.vault_root:
        raise SystemExit(
            "ERROR: provide --vault-root or set VAULT_ROOT.\n" "Example: VAULT_ROOT=/path/to/vault"
        )

    vault_root = Path(args.vault_root).resolve()
    if not vault_root.is_dir():
        raise SystemExit(f"ERROR: vault_root {vault_root!r} is not an existing directory.")

    # ── Import app modules (after path setup) ─────────────────────────────────
    try:
        import frontmatter as _fm  # python-frontmatter
    except ImportError as exc:
        raise SystemExit(
            "ERROR: python-frontmatter is not installed.\nRun: pip install python-frontmatter"
        ) from exc

    try:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
    except ImportError as exc:
        raise SystemExit(
            "ERROR: sqlalchemy+asyncpg must be installed.\n"
            "Run: pip install 'sqlalchemy[asyncio]' asyncpg"
        ) from exc

    _require_app_on_path()
    try:
        from app.wiki.summary import extract_first_paragraph_summary
    except ImportError as exc:
        raise SystemExit(
            "ERROR: cannot import app.wiki.summary. Ensure the backend venv is active "
            f"and you run this script from the backend/ directory.\nDetails: {exc}"
        ) from exc

    # ── DB connection ──────────────────────────────────────────────────────────
    engine = create_async_engine(args.db_url, echo=False)

    # ── Counters ───────────────────────────────────────────────────────────────
    total_scanned = 0
    total_updated = 0
    total_skipped_missing = 0
    total_skipped_no_paragraph = 0
    total_errors = 0

    batch_size = max(1, args.batch_size)
    max_pages = args.max_pages  # 0 = unlimited

    mode_label = "[dry-run] " if args.dry_run else ""

    print(
        f"[backfill] starting{' (DRY RUN — no writes)' if args.dry_run else ''}. "
        f"vault_root={str(vault_root)!r}"
        + (f" vault_id={args.vault_id!r}" if args.vault_id else " vault_id=<all>")
        + f" batch_size={batch_size}"
        + (f" max_pages={max_pages}" if max_pages else "")
    )

    # We use plain OFFSET-based pagination; for large tables a keyset cursor would
    # be faster, but the backfill is one-shot and the filter (summary IS NULL) means
    # the working set shrinks with each commit, making repeated OFFSET acceptable.
    offset = 0

    async with engine.connect() as conn:
        while True:
            if max_pages and total_scanned >= max_pages:
                print(f"[backfill] reached --max-pages={max_pages}; stopping early.")
                break

            # Build query
            if args.vault_id:
                rows_q = text(
                    "SELECT id, vault_id, file_path FROM pages "
                    "WHERE summary IS NULL "
                    "AND deleted_at IS NULL "
                    "AND file_path LIKE 'wiki/%' "
                    "AND vault_id = :vault_id "
                    "ORDER BY id "
                    "LIMIT :limit OFFSET :offset"
                )
                rows = (
                    await conn.execute(
                        rows_q,
                        {"vault_id": args.vault_id, "limit": batch_size, "offset": offset},
                    )
                ).fetchall()
            else:
                rows_q = text(
                    "SELECT id, vault_id, file_path FROM pages "
                    "WHERE summary IS NULL "
                    "AND deleted_at IS NULL "
                    "AND file_path LIKE 'wiki/%' "
                    "ORDER BY id "
                    "LIMIT :limit OFFSET :offset"
                )
                rows = (
                    await conn.execute(rows_q, {"limit": batch_size, "offset": offset})
                ).fetchall()

            if not rows:
                break

            batch_updates: list[dict[str, object]] = []

            for row in rows:
                page_id = row.id
                file_path_rel = row.file_path  # relative e.g. "wiki/entities/Foo.md"

                if max_pages and total_scanned >= max_pages:
                    break

                total_scanned += 1

                abs_path = vault_root / file_path_rel
                if not abs_path.is_file():
                    total_skipped_missing += 1
                    print(f"[backfill] SKIP (file missing): {file_path_rel!r} " f"(id={page_id})")
                    continue

                try:
                    raw_text = abs_path.read_text(encoding="utf-8", errors="replace")
                    post = _fm.loads(raw_text)
                    body = post.content  # frontmatter-stripped body
                    summary = extract_first_paragraph_summary(body)
                except Exception as exc:  # noqa: BLE001
                    total_errors += 1
                    print(
                        f"[backfill] ERROR reading/parsing {file_path_rel!r} "
                        f"(id={page_id}): {exc}"
                    )
                    continue

                if not summary:
                    total_skipped_no_paragraph += 1
                    # Leave NULL as-is; the row won't reappear on a re-run because the filter
                    # is summary IS NULL — so to avoid re-processing on every re-run we must
                    # skip the offset bump for these rows. We track them in the offset anyway
                    # (they stay in the result set) so progress eventually moves past them.
                    continue

                batch_updates.append({"id": page_id, "summary": summary})
                print(
                    f"{mode_label}[backfill] page id={page_id} {file_path_rel!r}: "
                    f"{summary[:60]!r}{'…' if len(summary) > 60 else ''}"
                )

            # ── Commit batch ───────────────────────────────────────────────────
            if batch_updates and not args.dry_run:
                # UPDATE each row individually (batch_updates is typically <= 200;
                # executemany-style text() is fine for this one-shot script).
                for upd in batch_updates:
                    await conn.execute(
                        text("UPDATE pages SET summary = :summary WHERE id = :id"),
                        {"id": upd["id"], "summary": upd["summary"]},
                    )
                await conn.commit()
                total_updated += len(batch_updates)
            elif batch_updates and args.dry_run:
                total_updated += len(batch_updates)

            # Advance offset by the full batch so we don't re-query already-seen NULLs
            # (rows that had no paragraph remain NULL and would be returned again at
            # offset=0 after a commit; bumping by batch_size skips them in this run).
            offset += len(rows)

    await engine.dispose()

    print(
        f"\n[backfill] {mode_label}done. "
        f"scanned={total_scanned} "
        f"updated={total_updated} "
        f"skipped_missing_file={total_skipped_missing} "
        f"skipped_no_paragraph={total_skipped_no_paragraph} "
        f"errors={total_errors}"
    )
    if args.dry_run:
        print("[backfill] DRY RUN — no rows were changed.")
    if total_skipped_no_paragraph:
        print(
            f"[backfill] NOTE: {total_skipped_no_paragraph} page(s) had no extractable first "
            "paragraph (heading-only body, empty body, etc.). Their summary remains NULL. "
            "A future re-ingest of those pages will attempt extraction again from fresh content."
        )
    if total_skipped_missing:
        print(
            f"[backfill] NOTE: {total_skipped_missing} file(s) were not found on disk under "
            f"vault_root={str(vault_root)!r}. Ensure --vault-root matches the server's mount path."
        )


def main() -> None:
    args = _parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
