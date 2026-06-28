#!/usr/bin/env python3
"""
seed_demo_vault.py — Obsidian-style demo vault seeder for graph visualisation QA.

Produces a scale-free graph (Barabási–Albert, m=2) so node degree varies widely —
a few hubs (degree 15-40), many leaves (degree 1-3) — making node sizes visibly
differ in the sigma.js viewer (ADR-0016 §2 visual goal: "più collegamenti → pallino
più grande").

IMPORTANT: inserts into the `links` table (NOT `edges`).  The GraphEngine reads
structural edges from `links` (direct) and `pages.sources` (shared source) — it
NEVER reads pre-inserted `edges` rows.  edges are populated by engine.recompute().

Usage:
    DATABASE_URL=postgresql+asyncpg://synapse:synapse@localhost:5432/synapse \\
        .venv/bin/python backend/scripts/seed_demo_vault.py --clear --nodes 140

    # Custom:
    .venv/bin/python backend/scripts/seed_demo_vault.py --nodes 140 \\
        --vault-id demo --db-url "postgresql+asyncpg://..."

Args:
    --nodes     Number of demo pages (default 140)
    --vault-id  Vault ID (default "default")
    --db-url    SQLAlchemy async DB URL (or set DATABASE_URL env var)
    --seed      RNG seed for reproducibility (default 42)
    --clear     Delete existing demo/ rows before inserting

Notes:
    - Does NOT touch fixture/ rows (seed_graph_fixture.py is unaffected).
    - Seeded RNG → same graph every run with same --seed.
    - Bumps vault_state.data_version to trigger GraphCache refresh.
    - asyncpg-safe: datetime objects for timestamptz, CAST(:x AS uuid), etc.

References:
    ADR-0016 §2 — structural_degree drives node size
    ADR-0016 §1 — links table → direct structural edges
    I1 — incremental; we write to links, not edges
    I7 — bounded; fixed --nodes cap
    G4 — seed_graph_fixture.py is separate and UNTOUCHED
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import uuid
from datetime import UTC, datetime
from typing import Any

# ── synthetic topic vocabulary ─────────────────────────────────────────────────
# Realistic-ish topic names drawn from a homelab / knowledge-base theme.
_TOPIC_PREFIXES = [
    "Attention", "Backpropagation", "Clustering", "Diffusion", "Embedding",
    "Fourier", "Gradient", "Hamiltonian", "Inference", "Jacobian",
    "Kernel", "Latent", "Manifold", "Normalisation", "Optimisation",
    "Perturbation", "Quantisation", "Regularisation", "Softmax", "Transformer",
    "Uncertainty", "Variational", "Wavelet", "XGBoost", "Zero-shot",
    "Attention Sink", "Batch Norm", "Cross-Entropy", "Dense Retrieval",
    "Encoder-Decoder", "Fine-Tuning", "Graph Neural Net", "Hidden State",
    "In-Context Learning", "Joint Embedding", "KV Cache", "LoRA", "Mixture",
    "Neural Scaling", "ONNX Runtime", "Parameter-Efficient", "QLoRA",
    "Retrieval-Augmented", "Sparse Attention", "Token Budget", "Universal Approx",
    "Vector DB", "Weight Tying", "eXplainability", "Yield Curve", "Z-Score",
    "Activation Function", "Beam Search", "Causal Mask", "Decoder-Only",
    "Early Stopping", "Flash Attention", "GELU", "Head Dimension",
    "Information Bottleneck", "Joint Training", "Knowledge Distillation",
    "Layer Norm", "Max Pooling", "Next-Token Prediction", "Output Head",
    "Pre-training", "Query-Key-Value", "ReLU", "Skip Connection",
    "Temperature Scaling", "UMAP", "Vocabulary Size", "Weight Decay",
    "Exploding Gradient", "Yield Prediction", "Zero Padding",
    "Adaptive Learning Rate", "Bias-Variance", "Contrastive Loss",
    "Dropout", "Entropy Regularisation", "Feature Extraction",
    "Gumbel Softmax", "Hyperparameter", "Implicit Differentiation",
    "Jacobi Method", "K-Nearest", "Long-Range Dependency",
    "Monte Carlo Tree", "Non-Linear Activation", "Online Learning",
    "Positional Encoding", "Quantile Loss", "Receptive Field",
    "Self-Supervised", "Transfer Learning", "Underfitting",
    "Vanishing Gradient", "Weight Initialisation", "eXtreme Gradient",
]

_TYPE_WEIGHTS = {
    "concept": 50,    # most common
    "entity": 25,
    "source": 12,
    "synthesis": 8,
    "comparison": 5,  # rarest
}


def _weighted_type(rng: random.Random) -> str:
    types = list(_TYPE_WEIGHTS.keys())
    weights = list(_TYPE_WEIGHTS.values())
    return rng.choices(types, weights=weights, k=1)[0]


def _make_titles(n: int, rng: random.Random) -> list[str]:
    """Generate n distinct synthetic topic titles."""
    pool = list(_TOPIC_PREFIXES)
    rng.shuffle(pool)
    titles = []
    idx = 0
    while len(titles) < n:
        if idx < len(pool):
            base = pool[idx]
            idx += 1
        else:
            # Beyond vocabulary: append a number
            base = rng.choice(pool) + f" #{len(titles) - len(pool) + 1}"
        if base not in titles:
            titles.append(base)
    return titles[:n]


# ── Barabási-Albert preferential-attachment graph ─────────────────────────────

def _ba_graph(n: int, m: int, rng: random.Random) -> list[tuple[int, int]]:
    """
    Generate a Barabási-Albert scale-free directed graph with n nodes and m
    attachments per new node.  Returns directed edges (new_node → existing_node).

    Returns list of (source_idx, target_idx) pairs.  These become directed links
    in the `links` table (resolved wikilinks).

    Parameters
    ----------
    n : int   total nodes
    m : int   edges added by each new node (BA parameter)
    rng       seeded RNG for reproducibility
    """
    assert m >= 1 and n > m, "BA requires n > m >= 1"
    edges: list[tuple[int, int]] = []

    # Start with a fully-connected seed clique of (m+1) nodes
    initial = list(range(m + 1))
    for i in range(len(initial)):
        for j in range(i + 1, len(initial)):
            edges.append((initial[i], initial[j]))
            edges.append((initial[j], initial[i]))

    # Repeated degree list for preferential attachment (Holme-Kim-style bucket)
    degree: list[int] = [0] * (m + 1)
    for a, b in edges:
        degree[a] += 1
        degree[b] += 1
    targets_pool: list[int] = []
    for node, deg in enumerate(degree):
        targets_pool.extend([node] * deg)

    for new_node in range(m + 1, n):
        degree.append(0)
        # Sample m distinct targets proportional to degree (preferential attachment)
        chosen: set[int] = set()
        attempts = 0
        max_attempts = m * 20
        while len(chosen) < m and attempts < max_attempts:
            t = rng.choice(targets_pool)
            if t != new_node:
                chosen.add(t)
            attempts += 1
        for t in chosen:
            edges.append((new_node, t))
            degree[new_node] += 1
            degree[t] += 1
            targets_pool.extend([new_node, t])  # update bucket

    return edges


def _make_pages(
    n: int,
    vault_id: str,
    rng: random.Random,
    source_ids: list[str],
) -> list[dict[str, Any]]:
    """Generate n demo page rows."""
    now = datetime.now(tz=UTC)
    titles = _make_titles(n, rng)
    rows = []
    for i in range(n):
        page_id = str(uuid.uuid4())
        ptype = _weighted_type(rng)
        # Most pages reference 0–2 shared source IDs.
        # A random ~30% of pages share sources with a cluster, rest have none.
        if rng.random() < 0.30:
            num_sources = rng.randint(1, 2)
            page_sources = rng.sample(source_ids, min(num_sources, len(source_ids)))
        else:
            page_sources = []

        rows.append(
            {
                "id": page_id,
                "vault_id": vault_id,
                "file_path": f"demo/{titles[i].lower().replace(' ', '_').replace('-', '_')}.md",
                "title": titles[i],
                "type": ptype,
                "sources": json.dumps(page_sources),
                "content_hash": f"demo{i:08x}",
                "source_mtime_ns": None,
                "qdrant_point_id": page_id,
                "x": None,
                "y": None,
                "deleted_at": None,
                "created_at": now,
                "updated_at": now,
            }
        )
    return rows


def _make_links(
    ba_edges: list[tuple[int, int]],
    page_ids: list[str],
    page_titles: list[str],
    vault_id: str,
) -> list[dict[str, Any]]:
    """
    Convert BA directed edges to resolved links table rows.
    Both directed directions are inserted (each is a separate wikilink).
    """
    now = datetime.now(tz=UTC)
    # Deduplicate same directed pair (BA already emits both directions for seed clique)
    seen: set[tuple[int, int]] = set()
    rows = []
    for src_idx, tgt_idx in ba_edges:
        if src_idx == tgt_idx:
            continue
        pair = (src_idx, tgt_idx)
        if pair in seen:
            continue
        seen.add(pair)
        rows.append(
            {
                "id": str(uuid.uuid4()),
                "source_page_id": page_ids[src_idx],
                "target_title": page_titles[tgt_idx],
                "target_page_id": page_ids[tgt_idx],
                "alias": None,
                "dangling": False,
                "created_at": now,
            }
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

    rng = random.Random(args.seed)  # noqa: S311

    # Shared source IDs: many small provenance clusters (2-3 pages each) so shared-source
    # edges stay SPARSE — the graph structure is driven by scale-free direct links, not
    # by dense source cliques (keeps the layout Obsidian-like, not a clump). See ADR-0016.
    num_sources = 24
    source_ids = [f"demo_src_{i:02d}" for i in range(num_sources)]

    print(f"[seed-demo] Generating {args.nodes} pages with BA(m=2) topology ...")
    pages = _make_pages(args.nodes, args.vault_id, rng, source_ids)
    page_ids = [str(p["id"]) for p in pages]
    page_titles = [str(p["title"]) for p in pages]

    ba_edges = _ba_graph(args.nodes, m=2, rng=rng)
    links = _make_links(ba_edges, page_ids, page_titles, args.vault_id)

    # Degree stats for info
    degrees: dict[int, int] = {}
    for s, t in ba_edges:
        degrees[s] = degrees.get(s, 0) + 1
        degrees[t] = degrees.get(t, 0) + 1
    deg_values = sorted(degrees.values(), reverse=True)
    print(
        f"[seed-demo] BA graph: {len(ba_edges)} directed link edges, "
        f"{len(links)} deduplicated link rows\n"
        f"  Degree stats: max={deg_values[0] if deg_values else 0} "
        f"min={deg_values[-1] if deg_values else 0} "
        f"median={deg_values[len(deg_values)//2] if deg_values else 0}"
    )

    engine = create_async_engine(db_url, echo=False)

    async with engine.begin() as conn:
        if args.clear:
            print(f"[seed-demo] Clearing existing demo/ rows for vault '{args.vault_id}'...")
            # Remove edges referencing demo pages first
            await conn.execute(
                text(
                    "DELETE FROM edges WHERE vault_id = :vid AND source_page_id IN "
                    "(SELECT id FROM pages WHERE vault_id = :vid AND file_path LIKE 'demo/%')"
                ),
                {"vid": args.vault_id},
            )
            # Remove links from demo pages
            await conn.execute(
                text(
                    "DELETE FROM links WHERE source_page_id IN "
                    "(SELECT id FROM pages WHERE vault_id = :vid AND file_path LIKE 'demo/%')"
                ),
                {"vid": args.vault_id},
            )
            # Remove links TO demo pages
            await conn.execute(
                text(
                    "DELETE FROM links WHERE target_page_id IN "
                    "(SELECT id FROM pages WHERE vault_id = :vid AND file_path LIKE 'demo/%')"
                ),
                {"vid": args.vault_id},
            )
            # Remove pages
            await conn.execute(
                text(
                    "DELETE FROM pages WHERE vault_id = :vid AND file_path LIKE 'demo/%'"
                ),
                {"vid": args.vault_id},
            )
            print("[seed-demo] Clear complete.")

        print(f"[seed-demo] Inserting {len(pages)} page rows...")
        for page in pages:
            await conn.execute(
                text(
                    "INSERT INTO pages "
                    "(id, vault_id, file_path, title, type, sources, content_hash, "
                    " source_mtime_ns, qdrant_point_id, x, y, deleted_at, created_at, updated_at) "
                    "VALUES (CAST(:id AS uuid), :vault_id, :file_path, :title, :type, "
                    " CAST(:sources AS jsonb), :content_hash, :source_mtime_ns, "
                    " CAST(:qdrant_point_id AS uuid), :x, :y, :deleted_at, "
                    " CAST(:created_at AS timestamptz), CAST(:updated_at AS timestamptz)) "
                    "ON CONFLICT (id) DO UPDATE SET "
                    " title = EXCLUDED.title, type = EXCLUDED.type, sources = EXCLUDED.sources, "
                    " updated_at = EXCLUDED.updated_at"
                ),
                page,
            )

        print(f"[seed-demo] Inserting {len(links)} link rows...")
        for link in links:
            await conn.execute(
                text(
                    "INSERT INTO links "
                    "(id, source_page_id, target_title, target_page_id, alias, dangling, created_at) "
                    "VALUES (CAST(:id AS uuid), CAST(:source_page_id AS uuid), :target_title, "
                    " CAST(:target_page_id AS uuid), :alias, :dangling, "
                    " CAST(:created_at AS timestamptz)) "
                    "ON CONFLICT DO NOTHING"
                ),
                link,
            )

        # Bump data_version so GraphCache triggers a recompute
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
            print("[seed-demo] Bumped vault_state.data_version.")
        else:
            now = datetime.now(tz=UTC)
            await conn.execute(
                text(
                    "INSERT INTO vault_state (id, vault_id, data_version, updated_at) "
                    "VALUES (CAST(:id AS uuid), :vid, 1, CAST(:now AS timestamptz))"
                ),
                {"id": str(uuid.uuid4()), "vid": args.vault_id, "now": now},
            )
            print("[seed-demo] Created vault_state row (data_version=1).")

    await engine.dispose()

    print(
        f"\n[seed-demo] Done.\n"
        f"  Pages:   {len(pages)}\n"
        f"  Links:   {len(links)} (directed wikilinks → resolved target_page_id)\n"
        f"  Vault:   {args.vault_id}\n"
        f"  Seed:    {args.seed}\n"
        f"\n"
        f"Next steps:\n"
        f"  1. Restart backend: docker compose restart synapse-backend\n"
        f"  2. curl http://localhost:8000/graph  -- triggers FA2 recompute on first MISS\n"
        f"  3. Expect: wide degree distribution, edge kind='link', few hundred edges total.\n"
    )


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Seed an Obsidian-style scale-free demo vault into Postgres."
    )
    p.add_argument("--nodes", type=int, default=140, help="Number of demo pages (default 140)")
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
        help="RNG seed for reproducibility (default 42)",
    )
    p.add_argument(
        "--clear",
        action="store_true",
        help="Delete existing demo/ rows (WHERE file_path LIKE 'demo/%%') before inserting",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    asyncio.run(_seed(args))


if __name__ == "__main__":
    main()
