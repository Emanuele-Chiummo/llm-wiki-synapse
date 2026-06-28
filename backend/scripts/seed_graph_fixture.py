#!/usr/bin/env python3
"""
seed_graph_fixture.py — G4 performance fixture seeder.

Writes N synthetic page rows with pre-set x/y coordinates + M edge rows into
Postgres, bypassing FA2. This lets the Playwright G4 test (graph-perf.spec.ts)
measure sigma.js WebGL render performance without requiring Ollama or AI models.

Usage:
    cd backend
    python scripts/seed_graph_fixture.py --nodes 200 --edges 500

    # Custom vault / DB URL:
    python scripts/seed_graph_fixture.py --nodes 200 --edges 500 \\
        --vault-id my-vault \\
        --db-url "postgresql+asyncpg://synapse:synapse@localhost:5432/synapse"

Prerequisites:
    - Postgres running (docker compose up db)
    - Backend venv activated: source .venv/bin/activate
    - Database already migrated: alembic upgrade head

Environment (alternative to --db-url):
    DATABASE_URL=postgresql+asyncpg://synapse:synapse@localhost:5432/synapse

Mock contract (for CI without GPU):
    This script writes only to Postgres tables (pages, vault_state, edges).
    No Ollama, no Qdrant, no filesystem writes. Coordinates are synthetic
    (random within [-500, 500] x [-500, 500]). Type alternates entity/concept.
    All source fields are set to a stable fixture sentinel.

What is seeded:
    - N pages rows: id (uuid), vault_id, file_path, title, type, content_hash,
      x (FA2-equivalent random), y, sources=[fixture_source], created_at, updated_at
    - vault_state: data_version bumped once (signals GraphCache to update)
    - M edges rows: random pairs from the N pages (canonical order: smaller uuid first)
      with a plausible random weight in [1.0, 12.5] and signals JSONB

References:
    - EC-M3-6 / AC-F4-7: G4 gate requires 200-node 500-edge fixture
    - ADR-0012: edge weight formula / signals shape
    - ADR-0013: pages.x/y columns
    - I7: fixture seed documented here (no real AI cost incurred)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import uuid
from datetime import UTC, datetime

# ── arg parser ────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Seed a graph performance fixture into Postgres.")
    p.add_argument("--nodes", type=int, default=200, help="Number of page nodes to create")
    p.add_argument("--edges", type=int, default=500, help="Number of edges to create")
    p.add_argument("--vault-id", default="default", help="Vault ID for all rows")
    p.add_argument(
        "--db-url",
        default=None,
        help="SQLAlchemy async DB URL (defaults to DATABASE_URL env var)",
    )
    p.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for coordinate/edge generation (reproducible fixture)",
    )
    p.add_argument(
        "--clear",
        action="store_true",
        help="Delete existing fixture rows (WHERE file_path LIKE 'fixture/%%') before inserting",
    )
    return p.parse_args()


# ── node/edge generators ──────────────────────────────────────────────────────


def _make_nodes(n: int, vault_id: str, rng: random.Random) -> list[dict[str, object]]:
    """Generate N synthetic page rows with random x/y in [-500, 500]."""
    page_types = ["entity", "concept", "synthesis", "comparison"]
    now = datetime.now(tz=UTC).isoformat()
    rows = []
    for i in range(n):
        page_id = str(uuid.uuid4())
        page_type = page_types[i % len(page_types)]
        # Spiral layout: gives a pleasing non-random look while still being synthetic
        angle = (2 * math.pi * i) / n * 3  # 3 full rotations
        radius = 50 + 400 * (i / max(n - 1, 1))
        x = radius * math.cos(angle) + rng.uniform(-10, 10)
        y = radius * math.sin(angle) + rng.uniform(-10, 10)
        rows.append(
            {
                "id": page_id,
                "vault_id": vault_id,
                "file_path": f"fixture/page_{i:04d}.md",
                "title": f"Fixture Node {i:04d}",
                "type": page_type,
                "sources": json.dumps([f"fixture_source_{i % 20}"]),
                "content_hash": f"fixture{i:08x}",
                "source_mtime_ns": None,
                "qdrant_point_id": page_id,
                "x": round(x, 4),
                "y": round(y, 4),
                "deleted_at": None,
                "created_at": now,
                "updated_at": now,
            }
        )
    return rows


def _make_edges(
    page_ids: list[str], m: int, vault_id: str, rng: random.Random
) -> list[dict[str, object]]:
    """Generate M unique edge rows between random pairs of page_ids."""
    now = datetime.now(tz=UTC).isoformat()
    seen: set[tuple[str, str]] = set()
    rows = []
    max_attempts = m * 10

    for _ in range(max_attempts):
        if len(rows) >= m:
            break
        a, b = rng.sample(page_ids, 2)
        pair = (min(a, b), max(a, b))  # canonical order: smaller uuid first (ADR-0012)
        if pair in seen:
            continue
        seen.add(pair)

        # Plausible random signals in range of the 4-signal formula
        direct = rng.choice([0.0, 3.0])
        source = rng.choice([0.0, 4.0])
        aa = round(rng.uniform(0.0, 1.5), 3)
        same_type = rng.choice([0.0, 1.0])
        weight = round(direct + source + aa + same_type, 4)
        if weight <= 0:
            weight = 1.0  # must be > 0 to be stored

        rows.append(
            {
                "id": str(uuid.uuid4()),
                "vault_id": vault_id,
                "source_page_id": pair[0],
                "target_page_id": pair[1],
                "weight": weight,
                "signals": json.dumps(
                    {"direct": direct, "source": source, "aa": aa, "type": same_type}
                ),
                "created_at": now,
            }
        )

    if len(rows) < m:
        print(
            f"[WARN] Could only generate {len(rows)} unique edges "
            f"(requested {m}; graph is too sparse — increase --nodes or reduce --edges)"
        )
    return rows


# ── DB operations ─────────────────────────────────────────────────────────────


async def _seed(args: argparse.Namespace) -> None:
    db_url = args.db_url or os.environ.get("DATABASE_URL")
    if not db_url:
        raise SystemExit(
            "ERROR: provide --db-url or set DATABASE_URL environment variable.\n"
            "Example: DATABASE_URL=postgresql+asyncpg://synapse:synapse@localhost:5432/synapse"
        )

    try:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
    except ImportError as exc:
        raise SystemExit(
            "ERROR: sqlalchemy and asyncpg must be installed.\n"
            "Run: pip install 'sqlalchemy[asyncio]' asyncpg"
        ) from exc

    rng = random.Random(args.seed)
    nodes = _make_nodes(args.nodes, args.vault_id, rng)
    page_ids = [n["id"] for n in nodes]  # type: ignore[index]
    edges = _make_edges(page_ids, args.edges, args.vault_id, rng)

    engine = create_async_engine(db_url, echo=False)

    async with engine.begin() as conn:
        if args.clear:
            print(f"[seed] Clearing existing fixture rows for vault '{args.vault_id}'...")
            await conn.execute(
                text(
                    "DELETE FROM edges WHERE vault_id = :vid AND source_page_id IN "
                    "(SELECT id FROM pages WHERE vault_id = :vid AND file_path LIKE 'fixture/%')"
                ),
                {"vid": args.vault_id},
            )
            await conn.execute(
                text("DELETE FROM pages WHERE vault_id = :vid AND file_path LIKE 'fixture/%'"),
                {"vid": args.vault_id},
            )

        print(f"[seed] Inserting {len(nodes)} page nodes for vault '{args.vault_id}'...")
        for node in nodes:
            await conn.execute(
                text(
                    "INSERT INTO pages "
                    "(id, vault_id, file_path, title, type, sources, content_hash, "
                    " source_mtime_ns, qdrant_point_id, x, y, deleted_at, created_at, updated_at) "
                    "VALUES (:id, :vault_id, :file_path, :title, :type, :sources, :content_hash, "
                    " :source_mtime_ns, :qdrant_point_id, :x, :y, :deleted_at, :created_at, :updated_at) "
                    "ON CONFLICT (id) DO UPDATE SET x = EXCLUDED.x, y = EXCLUDED.y, "
                    "updated_at = EXCLUDED.updated_at"
                ),
                node,
            )

        print(f"[seed] Inserting {len(edges)} edges...")
        for edge in edges:
            await conn.execute(
                text(
                    "INSERT INTO edges "
                    "(id, vault_id, source_page_id, target_page_id, weight, signals, created_at) "
                    "VALUES (:id, :vault_id, :source_page_id, :target_page_id, :weight, "
                    " CAST(:signals AS jsonb), :created_at) "
                    "ON CONFLICT (vault_id, source_page_id, target_page_id) "
                    "DO UPDATE SET weight = EXCLUDED.weight, signals = EXCLUDED.signals"
                ),
                edge,
            )

        # Bump data_version so the GraphCache knows to refresh
        result = await conn.execute(
            text("SELECT id FROM vault_state WHERE vault_id = :vid LIMIT 1"),
            {"vid": args.vault_id},
        )
        vs_row = result.one_or_none()
        if vs_row is not None:
            await conn.execute(
                text(
                    "UPDATE vault_state SET data_version = data_version + 1, "
                    "updated_at = NOW() WHERE vault_id = :vid"
                ),
                {"vid": args.vault_id},
            )
        else:
            now = datetime.now(tz=UTC).isoformat()
            await conn.execute(
                text(
                    "INSERT INTO vault_state (id, vault_id, data_version, updated_at) "
                    "VALUES (:id, :vid, 1, :now)"
                ),
                {"id": str(uuid.uuid4()), "vid": args.vault_id, "now": now},
            )

    await engine.dispose()

    print(
        f"[seed] Done.\n"
        f"  Nodes: {len(nodes)}\n"
        f"  Edges: {len(edges)}\n"
        f"  Vault: {args.vault_id}\n"
        f"  Seed:  {args.seed}\n"
        f"\n"
        f"Next steps:\n"
        f"  1. Start backend: uvicorn app.main:app --port 8000\n"
        f"  2. Start frontend: cd ../frontend && npm run dev\n"
        f"  3. Run Playwright: cd ../frontend && npx playwright test e2e/graph-perf.spec.ts\n"
        f"  4. Check docs/screens/ for committed PNG screenshots.\n"
    )


def main() -> None:
    args = _parse_args()
    asyncio.run(_seed(args))


if __name__ == "__main__":
    main()
