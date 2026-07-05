#!/usr/bin/env python3
"""
seed_e2e_extras.py — E2E precondition seeder for CI.

Seeds the additional rows that chat/provider E2E specs require but that
seed_demo_vault.py and seed_graph_fixture.py do NOT provide:

  (a) provider_config rows:
      - scope='operation', operation='chat', vault_id=<vault-id>,
        provider_type='local', model_id='qwen2.5:3b'
        (shell-m4-phase3-chat.spec.ts: "chat provider_config seeded: operation=chat, local/qwen2.5:3b")

  (b) 4 conversations with 2 messages each (vault_id=<vault-id>):
      (shell-m4-phase3-chat.spec.ts: "backend has 4+ seeded conversations")

The Alembic data migration 0002 already seeds 2 global provider_config rows
(api/claude-sonnet-4-6 + api/claude-haiku-4-5-20251001) — this script only
adds the operation-scoped chat row that the migration does not create.

Idempotent: existing rows of the correct shape are detected via SELECT before
INSERT, so re-running against the same database is safe.

Usage (CI):
    DATABASE_URL=postgresql+asyncpg://synapse:synapse@localhost:5432/synapse \\
        python backend/scripts/seed_e2e_extras.py --vault-id test

    # Or with explicit db-url:
    python backend/scripts/seed_e2e_extras.py \\
        --vault-id test \\
        --db-url "postgresql+asyncpg://synapse:synapse@localhost:5432/synapse"

Args:
    --vault-id  Vault ID for operation-scoped rows and conversations (default "test")
    --db-url    SQLAlchemy async DB URL (defaults to DATABASE_URL env var)

Notes:
    - Does NOT touch pages, links, edges, or ingest_runs (those are seeded by
      seed_demo_vault.py and seed_graph_fixture.py).
    - Conversations are seeded with realistic homelab/ML-topic titles so the
      conversation list UI is non-empty and clearly demo-labelled.
    - Messages use provider_type='local' / model_id='qwen2.5:3b' on assistant
      turns (matches the operation chat provider row) and cost=0.0 (I7: local).
    - asyncpg-safe: all timestamps are datetime objects, not ISO strings.

References:
    ADR-0008 §2 — provider_config scope / resolution precedence (operation wins)
    ADR-0019    — conversations + messages schema (chat tables, v0.4)
    I6          — chat provider MUST exist; operation scope wins; provider_type='local'
    I7          — cost fields present and logged; 0.0000 for local provider
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

# ── Conversation fixtures ──────────────────────────────────────────────────────

# 4 demo conversations covering homelab/ML topics.
# Each has 2 messages (user + assistant) so the message-list is non-empty.
# The assistant content is brief but non-trivial: specs only assert length > 0.
_CONVERSATION_FIXTURES: list[dict[str, Any]] = [
    {
        "title": "Transformer architecture overview",
        "user_msg": "What is a transformer architecture?",
        "assistant_msg": (
            "A transformer is a neural network architecture introduced in "
            "'Attention Is All You Need' (Vaswani et al., 2017). It replaces "
            "recurrence and convolution with a self-attention mechanism that "
            "processes tokens in parallel, enabling efficient training on long "
            "sequences. Transformers are the foundation of modern LLMs such as "
            "GPT-4 and Claude."
        ),
    },
    {
        "title": "Knowledge graph vs. relational DB",
        "user_msg": "How does a knowledge graph differ from a relational database?",
        "assistant_msg": (
            "A relational database stores data in fixed-schema tables joined by "
            "foreign keys. A knowledge graph stores entities (nodes) and typed "
            "relationships (edges), making it natural to represent heterogeneous "
            "connections and to traverse multi-hop paths. Synapse uses Postgres "
            "for metadata and a graph engine (igraph/FA2) for the knowledge graph "
            "layer on top of wikilink edges."
        ),
    },
    {
        "title": "RAG pipeline explained",
        "user_msg": "Explain Retrieval-Augmented Generation in simple terms.",
        "assistant_msg": (
            "RAG is a technique that combines a retrieval step with LLM generation. "
            "When you ask a question, the system first fetches the most relevant "
            "documents from a vector store (Qdrant here, using bge-m3 embeddings), "
            "then injects them as context into the LLM prompt. This grounds the "
            "answer in real documents and reduces hallucination."
        ),
    },
    {
        "title": "Synapse vault setup walkthrough",
        "user_msg": "How do I configure my first Synapse vault?",
        "assistant_msg": (
            "Start by setting VAULT_ID and VAULT_PATH in your docker-compose.yml. "
            "Place source documents under vault/raw/sources/, then trigger an ingest "
            "via POST /ingest. Synapse will analyse each file with the configured "
            "inference provider, write wiki pages to vault/wiki/, and build the "
            "knowledge graph. The .obsidian/ folder is auto-generated so the vault "
            "is immediately openable in Obsidian."
        ),
    },
]


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

    vault_id = args.vault_id
    now = datetime.now(tz=UTC)

    engine = create_async_engine(db_url, echo=False)

    async with engine.begin() as conn:
        # ── (a) provider_config — operation-scoped chat row ────────────────────
        # Check whether a chat operation row for this vault already exists.
        result = await conn.execute(
            text(
                "SELECT count(*) FROM provider_config "
                "WHERE scope = 'operation' AND operation = 'chat' AND vault_id = :vid"
            ),
            {"vid": vault_id},
        )
        chat_count = result.scalar_one()

        if chat_count == 0:
            await conn.execute(
                text(
                    "INSERT INTO provider_config "
                    "(scope, operation, vault_id, provider_type, model_id, "
                    " base_url, max_iter, token_budget, is_fallback, "
                    " created_at, updated_at) "
                    "VALUES "
                    "('operation', 'chat', :vid, 'local', 'qwen2.5:3b', "
                    " NULL, 3, 60000, false, "
                    " CAST(:now AS timestamptz), CAST(:now AS timestamptz))"
                ),
                {"vid": vault_id, "now": now},
            )
            print(
                f"[seed-e2e] Inserted provider_config: "
                f"scope=operation, operation=chat, vault_id={vault_id!r}, "
                f"provider_type=local, model_id=qwen2.5:3b"
            )
        else:
            print(
                f"[seed-e2e] provider_config (operation/chat/{vault_id}) already exists "
                f"({chat_count} row(s)) — skipping insert."
            )

        # ── (b) conversations + messages ───────────────────────────────────────
        # Count live conversations already in the vault.
        result = await conn.execute(
            text(
                "SELECT count(*) FROM conversations " "WHERE vault_id = :vid AND deleted_at IS NULL"
            ),
            {"vid": vault_id},
        )
        existing_count = result.scalar_one()

        needed = max(0, len(_CONVERSATION_FIXTURES) - existing_count)
        if needed == 0:
            print(
                f"[seed-e2e] conversations: {existing_count} live row(s) already exist "
                f"for vault {vault_id!r} — skipping."
            )
        else:
            print(
                f"[seed-e2e] conversations: {existing_count} exist; "
                f"inserting {needed} more (target: {len(_CONVERSATION_FIXTURES)})..."
            )
            fixtures_to_seed = _CONVERSATION_FIXTURES[existing_count:]

            for i, fixture in enumerate(fixtures_to_seed):
                # Each conversation is offset slightly into the past so the list
                # is ordered naturally (most recent first).
                conv_offset = timedelta(minutes=(len(fixtures_to_seed) - i) * 10)
                conv_created = now - conv_offset
                conv_id = str(uuid.uuid4())

                await conn.execute(
                    text(
                        "INSERT INTO conversations "
                        "(id, vault_id, title, created_at, updated_at, deleted_at) "
                        "VALUES "
                        "(CAST(:id AS uuid), :vault_id, :title, "
                        " CAST(:created_at AS timestamptz), "
                        " CAST(:updated_at AS timestamptz), NULL)"
                    ),
                    {
                        "id": conv_id,
                        "vault_id": vault_id,
                        "title": fixture["title"],
                        "created_at": conv_created,
                        "updated_at": conv_created,
                    },
                )

                # user message — created immediately at conversation start
                user_created = conv_created + timedelta(seconds=1)
                await conn.execute(
                    text(
                        "INSERT INTO messages "
                        "(id, conversation_id, role, content, citations, "
                        " provider_type, model_id, input_tokens, output_tokens, "
                        " total_cost_usd, created_at) "
                        "VALUES "
                        "(CAST(:id AS uuid), CAST(:conv_id AS uuid), "
                        " 'user', :content, CAST(:citations AS jsonb), "
                        " NULL, NULL, 0, 0, 0, "
                        " CAST(:created_at AS timestamptz))"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "conv_id": conv_id,
                        "content": fixture["user_msg"],
                        "citations": json.dumps([]),
                        "created_at": user_created,
                    },
                )

                # assistant message — arrives a few seconds later
                asst_created = user_created + timedelta(seconds=3)
                await conn.execute(
                    text(
                        "INSERT INTO messages "
                        "(id, conversation_id, role, content, citations, "
                        " provider_type, model_id, input_tokens, output_tokens, "
                        " total_cost_usd, created_at) "
                        "VALUES "
                        "(CAST(:id AS uuid), CAST(:conv_id AS uuid), "
                        " 'assistant', :content, CAST(:citations AS jsonb), "
                        " 'local', 'qwen2.5:3b', 128, 96, 0, "
                        " CAST(:created_at AS timestamptz))"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "conv_id": conv_id,
                        "content": fixture["assistant_msg"],
                        "citations": json.dumps([]),
                        "created_at": asst_created,
                    },
                )

                # Bump conversation.updated_at to match the last message time
                await conn.execute(
                    text(
                        "UPDATE conversations SET updated_at = CAST(:ts AS timestamptz) "
                        "WHERE id = CAST(:id AS uuid)"
                    ),
                    {"ts": asst_created, "id": conv_id},
                )

            print(
                f"[seed-e2e] Inserted {needed} conversations "
                f"with 2 messages each (user + assistant)."
            )

    await engine.dispose()

    print(
        f"\n[seed-e2e] Done.\n"
        f"  Vault:           {vault_id}\n"
        f"  provider_config: operation=chat, local/qwen2.5:3b "
        f"{'(pre-existing)' if chat_count > 0 else '(inserted)'}\n"
        f"  conversations:   {existing_count + needed} total "
        f"({needed} inserted, {existing_count} pre-existing)\n"
        f"\n"
        f"These rows satisfy:\n"
        f"  shell-m4-phase3-chat.spec.ts: "
        f"'chat provider_config seeded: operation=chat, local/qwen2.5:3b'\n"
        f"  shell-m4-phase3-chat.spec.ts: 'backend has 4+ seeded conversations'\n"
    )


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Seed E2E preconditions: chat provider_config + conversations."
    )
    p.add_argument(
        "--vault-id",
        default="test",
        help="Vault ID for operation-scoped rows and conversations (default 'test')",
    )
    p.add_argument(
        "--db-url",
        default=None,
        help="SQLAlchemy async DB URL (defaults to DATABASE_URL env var)",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    asyncio.run(_seed(args))


if __name__ == "__main__":
    main()
