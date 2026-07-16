#!/usr/bin/env python3
"""
backfill_qdrant_vault_id.py — one-shot backfill for BE-PERF-3.

BE-PERF-3 added ``vault_id`` to the Qdrant point payload (``app.qdrant_client.upsert_point``)
so Phase-1 vector search (``app.rag.retrieval._phase1_vector_search``) can scope its dense
top-k query to the active vault. Points written BEFORE this fix landed have no ``vault_id``
field in their payload and will never match that filter.

This is a MANUAL, one-shot, operator-run script. It is NOT run automatically at startup and
is NOT part of any request path — running it (or not) never blocks ingest, search, or chat.
Until it has been run, ``_phase1_vector_search`` falls back to an UNFILTERED query ONLY for
the instance's default/legacy vault (``settings.vault_id``) when the filtered search returns
zero hits, logging a one-time warning. Multi-vault deployments with real secondary vaults
should run this backfill promptly, since the unfiltered fallback does not apply to them.

What it does:
    1. Scrolls the ``synapse_pages`` Qdrant collection in bounded batches (``--batch-size``,
       default 256), looking only at points whose payload has no ``vault_id`` key.
    2. For each such point, resolves ``vault_id`` from Postgres:
       - Primary: join by point id == ``pages.id`` (ADR-0002 — the point id IS the page's
         primary key, so this is an exact, race-free join. No file_path matching needed).
       - Fallback: if the page row no longer exists (deleted since the point was written),
         the point is left AS-IS and logged as "orphaned" — it will keep degrading to the
         unfiltered fallback path (harmless: it will simply never be returned by a
         vault-scoped filter query for a vault other than the default one) until a future
         reconciliation pass or manual cleanup removes it.
    3. Calls ``set_payload`` per resolved batch to add ``vault_id`` — never touches the
       vector, ``file_path``, ``title``, or ``type`` fields (I1 — targeted, incremental).
    4. Prints a summary: points scanned, points updated, points orphaned (left unresolved).

This script is READ-then-WRITE per batch; it can be safely re-run (idempotent — points that
already carry ``vault_id`` are skipped by the scroll filter) and safely interrupted/resumed
(the Qdrant scroll ``offset`` cursor is NOT persisted across runs — simply re-run the script;
already-backfilled points are skipped).

Usage:
    cd backend
    python scripts/backfill_qdrant_vault_id.py --dry-run          # report only, no writes
    python scripts/backfill_qdrant_vault_id.py                    # apply the backfill

    # Custom endpoints (defaults come from env vars, matching Settings):
    python scripts/backfill_qdrant_vault_id.py \\
        --qdrant-url http://localhost:6333 \\
        --collection synapse_pages \\
        --db-url "postgresql+asyncpg://synapse:synapse@localhost:5432/synapse"

Environment (alternative to CLI flags):
    QDRANT_URL, QDRANT_API_KEY, QDRANT_COLLECTION (defaults: http://localhost:6333, unset,
    synapse_pages — matches app.config.Settings field names/defaults)
    DATABASE_URL=postgresql+asyncpg://synapse:synapse@localhost:5432/synapse

Prerequisites:
    - Qdrant + Postgres reachable from wherever this script runs.
    - Backend venv activated: source .venv/bin/activate (needs qdrant-client + sqlalchemy+asyncpg).

References:
    - BE-PERF-3 (this fix): app.qdrant_client.upsert_point, app.rag.retrieval._phase1_vector_search,
      app.rag.retrieval._load_page_meta.
    - ADR-0002: point id == pages.id (the join key used here).
    - I1: this script performs targeted point updates, never a full collection recreate.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from typing import Any


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Backfill vault_id into existing Qdrant point payloads (BE-PERF-3)."
    )
    p.add_argument(
        "--qdrant-url",
        default=os.environ.get("QDRANT_URL", "http://localhost:6333"),
        help="Qdrant base URL (default: $QDRANT_URL or http://localhost:6333)",
    )
    p.add_argument(
        "--qdrant-api-key",
        default=os.environ.get("QDRANT_API_KEY"),
        help="Qdrant API key, if auth is enabled (default: $QDRANT_API_KEY)",
    )
    p.add_argument(
        "--collection",
        default=os.environ.get("QDRANT_COLLECTION", "synapse_pages"),
        help="Qdrant collection name (default: $QDRANT_COLLECTION or synapse_pages)",
    )
    p.add_argument(
        "--db-url",
        default=os.environ.get("DATABASE_URL"),
        help="SQLAlchemy async Postgres URL (default: $DATABASE_URL)",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=256,
        help="Points fetched per Qdrant scroll page (default: 256)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be updated without calling set_payload",
    )
    return p.parse_args()


async def _run(args: argparse.Namespace) -> None:
    if not args.db_url:
        raise SystemExit(
            "ERROR: provide --db-url or set DATABASE_URL environment variable.\n"
            "Example: DATABASE_URL=postgresql+asyncpg://synapse:synapse@localhost:5432/synapse"
        )

    try:
        from qdrant_client.http import models as qmodels

        from qdrant_client import AsyncQdrantClient
    except ImportError as exc:  # pragma: no cover - environment guard
        raise SystemExit(
            "ERROR: qdrant-client must be installed.\nRun: pip install qdrant-client"
        ) from exc

    try:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
    except ImportError as exc:  # pragma: no cover - environment guard
        raise SystemExit(
            "ERROR: sqlalchemy and asyncpg must be installed.\n"
            "Run: pip install 'sqlalchemy[asyncio]' asyncpg"
        ) from exc

    qdrant = AsyncQdrantClient(url=args.qdrant_url, api_key=args.qdrant_api_key)
    engine = create_async_engine(args.db_url, echo=False)

    # Only scroll points that do NOT already carry vault_id — idempotent re-runs skip
    # already-backfilled points automatically (no local cursor bookkeeping needed).
    # IsEmptyCondition matches points where the field is missing OR null/empty.
    missing_vault_id_filter = qmodels.Filter(
        must=[qmodels.IsEmptyCondition(is_empty=qmodels.PayloadField(key="vault_id"))]
    )

    scanned = 0
    updated = 0
    orphaned = 0
    offset: Any = None

    async with engine.connect() as conn:
        while True:
            records, offset = await qdrant.scroll(
                collection_name=args.collection,
                scroll_filter=missing_vault_id_filter,
                limit=args.batch_size,
                with_payload=True,
                with_vectors=False,
                offset=offset,
            )
            if not records:
                break

            scanned += len(records)
            point_ids = [str(r.id) for r in records]
            placeholders = ",".join(f":p{i}" for i in range(len(point_ids)))
            binds = {f"p{i}": pid for i, pid in enumerate(point_ids)}
            result = await conn.execute(
                text(
                    f"SELECT id, vault_id FROM pages "  # noqa: S608 - app-generated placeholders only
                    f"WHERE CAST(id AS TEXT) IN ({placeholders})"
                ),
                binds,
            )
            vault_id_by_page: dict[str, str] = {str(row.id): row.vault_id for row in result}

            # Group by resolved vault_id so each set_payload call batches points that share one.
            by_vault: dict[str, list[str]] = {}
            for pid in point_ids:
                vid = vault_id_by_page.get(pid)
                if vid is None:
                    orphaned += 1
                    print(f"[backfill] orphaned point (no matching pages row): {pid}")
                    continue
                by_vault.setdefault(vid, []).append(pid)

            for vault_id, pids in by_vault.items():
                updated += len(pids)
                if args.dry_run:
                    print(f"[dry-run] would set vault_id={vault_id!r} on {len(pids)} point(s)")
                    continue
                await qdrant.set_payload(
                    collection_name=args.collection,
                    payload={"vault_id": vault_id},
                    points=qmodels.PointIdsList(points=pids),
                )
                print(f"[backfill] set vault_id={vault_id!r} on {len(pids)} point(s)")

            if offset is None:
                break

    await engine.dispose()

    mode = "DRY-RUN — " if args.dry_run else ""
    print(
        f"[backfill] {mode}done. scanned={scanned} updated={updated} orphaned={orphaned} "
        f"collection={args.collection!r}"
    )
    if orphaned:
        print(
            "[backfill] NOTE: orphaned points have no matching Postgres pages row (deleted "
            "since the point was written). They are left without vault_id; consider a "
            "separate cleanup pass (delete_point) if they are confirmed stale."
        )


def main() -> None:
    args = _parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
