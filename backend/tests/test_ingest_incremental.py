"""
Integration tests for incremental ingest logic (I1 / G1 property).

These tests exercise the full ingest_file() / delete_file() code path using:
  - SQLite in-memory database (via aiosqlite) — no Postgres needed
  - FakeQdrantClient — in-process Qdrant stub
  - FakeEmbeddingClient (already in embeddings.py)

Coverage:
  AC-WATCH-1  new file → DB row + Qdrant point created
  AC-WATCH-2  same file re-dropped (unchanged) → NO duplicate row, NO new embedding call
  AC-WATCH-3  modified file → row updated in-place (updated_at > before, new hash)
  AC-WATCH-4  deleted file → deleted_at set; Qdrant point removed
  AC-WATCH-5  startup does NOT trigger rescan (structural: watcher.start() registers
              handler only — no ingest called)
  AC-WATCH-6  EMBEDDING_URL is the only embedding source (FakeEmbeddingClient.call_count)
  AC-K4-1     one log line per indexed file, correct format
  AC-K4-2     log.md never truncated (only grows)
  AC-K4-3     duplicate ingest of unchanged file does NOT append log line
  AC-F16dv-2  data_version increments on each successful ingest
  AC-F16dv-4  data_version never decremented by skip/delete/restart

Test IDs: T-INC-001 .. T-INC-015

Infrastructure approach:
  - SQLite+aiosqlite: uses SQLAlchemy create_async_engine("sqlite+aiosqlite:///:memory:")
  - JSONB→JSON: the JSONB SQLAlchemy column type is overridden to native JSON for SQLite
  - FakeQdrantClient: in-memory dict; implements upsert/delete/retrieve
  - FakeEmbeddingClient: from embeddings.py (call_count tracked)
  - All network I/O is patched — no external services needed

DEFERRED (needs live Postgres+Qdrant on TrueNAS):
  The full timing assertion (AC-WATCH-1: "within 5 seconds from filesystem event")
  and Qdrant direct-API verify (AC-QD-2/3) require live infrastructure.
  Those are marked DEFERRED-needs-live-infra in TRACEABILITY.md.
"""

from __future__ import annotations

import re
import time
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# ── SQLite engine factory ──────────────────────────────────────────────────────
# We must patch JSONB → JSON before importing models
# (SQLAlchemy models use postgresql.JSONB; SQLite doesn't have it)
import sqlalchemy.dialects.postgresql as pg_dialects
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# Monkey-patch JSONB at import time so models.py Base.metadata uses JSON
_original_jsonb = pg_dialects.JSONB


def _make_sqlite_engine(db_url: str = "sqlite+aiosqlite:///:memory:") -> Any:
    """Create an async SQLite engine with the same settings as SQLAlchemy async."""
    return create_async_engine(
        db_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


# ── FakeQdrantClient ──────────────────────────────────────────────────────────


class FakeQdrantClient:
    """
    In-memory Qdrant stub.

    Stores points in a dict keyed by str(uuid).
    Implements the minimal async interface used by qdrant_client.py.
    """

    def __init__(self) -> None:
        self.points: dict[str, dict[str, Any]] = {}  # point_id_str → payload
        self.vectors: dict[str, list[float]] = {}
        self.collections: dict[str, dict[str, Any]] = {}
        self.upsert_calls: list[tuple[str, str, list[float]]] = []  # (collection, id, vec)
        self.delete_calls: list[tuple[str, str]] = []  # (collection, id)

    async def get_collections(self) -> MagicMock:
        mock = MagicMock()
        mock.collections = [MagicMock(name=name) for name in self.collections]
        return mock

    async def create_collection(self, collection_name: str, vectors_config: Any) -> None:
        self.collections[collection_name] = {"vectors_config": vectors_config}

    async def get_collection(self, collection_name: str) -> MagicMock:
        info = MagicMock()
        cfg = self.collections.get(collection_name, {}).get("vectors_config")
        params = MagicMock()
        if cfg is not None:
            params.vectors = cfg
        info.config.params = params
        return info

    async def upsert(self, collection_name: str, points: list[Any]) -> None:
        for pt in points:
            pid = str(pt.id)
            self.points[pid] = pt.payload or {}
            self.vectors[pid] = pt.vector or []
            self.upsert_calls.append((collection_name, pid, pt.vector or []))

    async def delete(self, collection_name: str, points_selector: Any) -> None:
        for pid in points_selector.points:
            pid_str = str(pid)
            self.points.pop(pid_str, None)
            self.vectors.pop(pid_str, None)
            self.delete_calls.append((collection_name, pid_str))

    async def retrieve(self, collection_name: str, ids: list[Any]) -> list[MagicMock]:
        result = []
        for pid in ids:
            pid_str = str(pid)
            if pid_str in self.points:
                mock_pt = MagicMock()
                mock_pt.id = pid_str
                mock_pt.payload = self.points[pid_str]
                mock_pt.vector = self.vectors.get(pid_str, [])
                result.append(mock_pt)
        return result

    def point_exists(self, page_id: uuid.UUID) -> bool:
        return str(page_id) in self.points

    def point_count_for_file(self, file_path: str) -> int:
        return sum(1 for p in self.points.values() if p.get("file_path") == file_path)


# ── Shared fixture: patched ingest environment ────────────────────────────────


@pytest.fixture()
async def ingest_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """
    Full ingest environment:
    - SQLite in-memory DB with pages + vault_state tables
    - FakeQdrantClient
    - FakeEmbeddingClient
    - Temporary vault directory
    - Patched settings pointing to temp vault

    Returns a dict with keys: db_session, qdrant, embedding, vault_root, sources_dir
    """
    from app import config as cfg
    from app.embeddings import FakeEmbeddingClient, set_embedding_client

    # ── Vault filesystem ──────────────────────────────────────────────────────
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    sources_dir = vault_root / "raw" / "sources"
    sources_dir.mkdir(parents=True)
    wiki_dir = vault_root / "wiki"
    wiki_dir.mkdir()
    log_md = wiki_dir / "log.md"
    log_md.write_text("---\ntype: log\ntitle: Synapse Ingest Log\n---\n\n", encoding="utf-8")

    # ── Patch settings ────────────────────────────────────────────────────────
    monkeypatch.setattr(cfg.settings, "vault_path", str(vault_root))
    monkeypatch.setattr(cfg.settings, "vault_id", "test-vault")
    monkeypatch.setattr(type(cfg.settings), "vault_root", property(lambda self: vault_root))
    monkeypatch.setattr(type(cfg.settings), "raw_sources_dir", property(lambda self: sources_dir))
    monkeypatch.setattr(type(cfg.settings), "wiki_dir", property(lambda self: wiki_dir))
    monkeypatch.setattr(type(cfg.settings), "log_md_path", property(lambda self: log_md))

    # ── SQLite engine + tables ────────────────────────────────────────────────
    from sqlalchemy import Column, Integer, String

    # For SQLite, JSONB → JSON; UUID → String; TIMESTAMP → String (simplified)
    # We patch the column types at the engine level using `type_descriptor`
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Create tables using the models' metadata, but with SQLite-compatible type overrides
    # Strategy: use `render_as_batch=True` + `include_schemas=False` approach;
    # actually the simplest approach for SQLite compat is to define the schema imperatively.
    from sqlalchemy import (
        BigInteger,
        Float,
        MetaData,
        Table,
        Text,
    )

    meta = MetaData()

    Table(
        "pages",
        meta,
        Column("id", String(36), primary_key=True),
        Column("vault_id", String, nullable=False),
        Column("file_path", Text, nullable=False),
        Column("title", Text, nullable=True),
        Column("type", Text, nullable=True),
        Column("sources", Text, nullable=True),  # JSON stored as text in SQLite
        Column("content_hash", String(64), nullable=False),
        Column("source_mtime_ns", BigInteger, nullable=True),
        Column("qdrant_point_id", String(36), nullable=True),
        Column("x", Float, nullable=True),  # v0.3: FR coords (ADR-0013)
        Column("y", Float, nullable=True),  # v0.3: FR coords (ADR-0013)
        Column("pinned", Integer, nullable=False, server_default=sa_text("0")),  # Feature A
        Column("deleted_at", Text, nullable=True),
        Column("created_at", Text, nullable=False),
        Column("updated_at", Text, nullable=False),
    )

    Table(
        "vault_state",
        meta,
        Column("id", String(36), primary_key=True),
        Column("vault_id", String, nullable=False, unique=True),
        Column("data_version", Integer, nullable=False, default=0),
        Column("updated_at", Text, nullable=False),
    )

    async with engine.begin() as conn:
        await conn.run_sync(meta.create_all)

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    # Seed vault_state
    async with session_factory() as session:
        await session.execute(
            sa_text(
                "INSERT INTO vault_state (id, vault_id, data_version, updated_at) "
                "VALUES (:id, :vault_id, 0, datetime('now'))"
            ),
            {"id": str(uuid.uuid4()), "vault_id": "test-vault"},
        )
        await session.commit()

    # ── Fake clients ──────────────────────────────────────────────────────────
    fake_embedding = FakeEmbeddingClient(dim=8)  # tiny dimension for speed
    set_embedding_client(fake_embedding)

    fake_qdrant = FakeQdrantClient()
    fake_qdrant.collections["synapse_pages"] = {}

    # ── Patch db.get_session and qdrant_client ─────────────────────────────────
    from contextlib import asynccontextmanager

    @asynccontextmanager  # type: ignore[arg-type]
    async def patched_get_session():  # type: ignore[no-untyped-def]
        async with session_factory() as sess:
            try:
                yield sess
                await sess.commit()
            except Exception:
                await sess.rollback()
                raise

    monkeypatch.setattr("app.db.get_session", patched_get_session)
    monkeypatch.setattr("app.ingest.orchestrator.get_session", patched_get_session)

    # Patch qdrant operations
    monkeypatch.setattr("app.qdrant_client.get_qdrant_client", lambda: fake_qdrant)
    monkeypatch.setattr(
        "app.ingest.orchestrator.upsert_point",
        lambda **kwargs: fake_qdrant.upsert(
            "synapse_pages",
            [
                type(
                    "Pt",
                    (),
                    {
                        "id": str(kwargs["page_id"]),
                        "vector": kwargs["vector"],
                        "payload": {
                            "file_path": kwargs["file_path"],
                            "title": kwargs["title"],
                            "type": kwargs["page_type"],
                        },
                    },
                )()
            ],
        ),
    )
    monkeypatch.setattr(
        "app.ingest.orchestrator.delete_point",
        lambda page_id: fake_qdrant.delete(
            "synapse_pages",
            type("Sel", (), {"points": [str(page_id)]})(),
        ),
    )

    yield {
        "session_factory": session_factory,
        "qdrant": fake_qdrant,
        "embedding": fake_embedding,
        "vault_root": vault_root,
        "sources_dir": sources_dir,
        "wiki_dir": wiki_dir,
        "log_md": log_md,
        "meta": meta,
        "engine": engine,
    }

    # Cleanup: reset embedding client to avoid cross-test pollution
    set_embedding_client(None)  # type: ignore[arg-type]


# ── Helpers ────────────────────────────────────────────────────────────────────


async def _row_count(env: dict[str, Any], file_path_rel: str) -> int:
    """Count live pages rows for a relative file path."""
    async with env["session_factory"]() as session:
        result = await session.execute(
            sa_text("SELECT COUNT(*) FROM pages WHERE file_path = :fp AND deleted_at IS NULL"),
            {"fp": file_path_rel},
        )
        return result.scalar_one()


async def _get_row(env: dict[str, Any], file_path_rel: str) -> dict[str, Any] | None:
    """Fetch a single live pages row as a dict."""
    async with env["session_factory"]() as session:
        result = await session.execute(
            sa_text("SELECT * FROM pages WHERE file_path = :fp AND deleted_at IS NULL"),
            {"fp": file_path_rel},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row is not None else None


async def _get_data_version(env: dict[str, Any]) -> int:
    """Return the current data_version from vault_state."""
    async with env["session_factory"]() as session:
        result = await session.execute(
            sa_text("SELECT data_version FROM vault_state WHERE vault_id = 'test-vault'")
        )
        return result.scalar_one()


def _log_lines(env: dict[str, Any]) -> list[str]:
    """Return non-empty, non-frontmatter lines from log.md."""
    text = env["log_md"].read_text(encoding="utf-8")
    return [
        line
        for line in text.splitlines()
        if line.strip()
        and not line.startswith("---")
        and not line.startswith("type:")
        and not line.startswith("title:")
        and not line.startswith("<!--")
    ]


LOG_LINE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z \| INDEXED \| .+$")


# ── AC-WATCH-1: new file → DB row ─────────────────────────────────────────────


class TestNewFileIngest:
    """T-INC-001 — AC-WATCH-1"""

    async def test_new_file_creates_db_row_and_qdrant_point(
        self, ingest_env: dict[str, Any]
    ) -> None:
        """T-INC-001: Ingest a new file → exactly 1 DB row + 1 Qdrant point."""
        from app.ingest.orchestrator import ingest_file

        src = ingest_env["sources_dir"] / "new_file.md"
        src.write_text(
            "---\ntype: entity\ntitle: New\nsources: []\n---\n\nBody.\n",
            encoding="utf-8",
        )
        rel = str(src.relative_to(ingest_env["vault_root"]))

        result = await ingest_file(src)

        assert result.status == "completed"
        assert await _row_count(ingest_env, rel) == 1, "Exactly 1 live DB row must exist"
        assert ingest_env["qdrant"].point_exists(
            result.page_id
        ), "A Qdrant point must be created for the ingested page"

    async def test_new_file_embedding_called_exactly_once(self, ingest_env: dict[str, Any]) -> None:
        """T-INC-002: Embedding client called exactly once per new file ingest."""
        from app.ingest.orchestrator import ingest_file

        src = ingest_env["sources_dir"] / "embed_once.md"
        src.write_text("---\ntype: concept\ntitle: E\nsources: []\n---\n", encoding="utf-8")

        calls_before = ingest_env["embedding"].call_count
        await ingest_file(src)
        calls_after = ingest_env["embedding"].call_count

        assert calls_after - calls_before == 1, (
            f"Embedding must be called exactly once per ingest; "
            f"got {calls_after - calls_before} calls"
        )


# ── AC-WATCH-2: unchanged file re-drop → no duplicate ────────────────────────


class TestUnchangedFileSkip:
    """T-INC-003, T-INC-004 — AC-WATCH-2, AC-K4-3, I1"""

    async def test_unchanged_file_does_not_create_duplicate_row(
        self, ingest_env: dict[str, Any]
    ) -> None:
        """T-INC-003: Same file ingested twice → still exactly 1 DB row (I1)."""
        from app.ingest.orchestrator import ingest_file

        src = ingest_env["sources_dir"] / "idempotent.md"
        src.write_text("---\ntype: entity\ntitle: Idem\nsources: []\n---\n", encoding="utf-8")
        rel = str(src.relative_to(ingest_env["vault_root"]))

        await ingest_file(src)
        first_count = await _row_count(ingest_env, rel)

        result2 = await ingest_file(src)  # same mtime + same content
        second_count = await _row_count(ingest_env, rel)

        assert first_count == 1
        assert second_count == 1, "Duplicate ingest must not create a new row (I1)"
        assert result2.status == "skipped", "Second ingest must return status='skipped'"

    async def test_unchanged_file_does_not_create_duplicate_qdrant_point(
        self, ingest_env: dict[str, Any]
    ) -> None:
        """T-INC-004: Same file ingested twice → still exactly 1 Qdrant point (I1)."""
        from app.ingest.orchestrator import ingest_file

        src = ingest_env["sources_dir"] / "qdrant_idem.md"
        src.write_text("---\ntype: entity\ntitle: Q\nsources: []\n---\n", encoding="utf-8")

        await ingest_file(src)
        upserts_before = len(ingest_env["qdrant"].upsert_calls)

        await ingest_file(src)
        upserts_after = len(ingest_env["qdrant"].upsert_calls)

        assert (
            upserts_after == upserts_before
        ), "Qdrant upsert must NOT be called on a skipped (unchanged) file re-ingest (I1)"

    async def test_unchanged_file_does_not_append_log_line(
        self, ingest_env: dict[str, Any]
    ) -> None:
        """T-INC-005: AC-K4-3 — duplicate ingest must NOT append a log line."""
        from app.ingest.orchestrator import ingest_file

        src = ingest_env["sources_dir"] / "log_idem.md"
        src.write_text("---\ntype: entity\ntitle: L\nsources: []\n---\n", encoding="utf-8")

        await ingest_file(src)
        lines_after_first = len(_log_lines(ingest_env))

        await ingest_file(src)
        lines_after_second = len(_log_lines(ingest_env))

        assert (
            lines_after_second == lines_after_first
        ), "log.md must NOT grow on duplicate ingest of unchanged file (AC-K4-3)"

    async def test_unchanged_file_does_not_bump_data_version(
        self, ingest_env: dict[str, Any]
    ) -> None:
        """T-INC-006: AC-F16dv-4 — skip does not increment data_version."""
        from app.ingest.orchestrator import ingest_file

        src = ingest_env["sources_dir"] / "version_skip.md"
        src.write_text("---\ntype: entity\ntitle: V\nsources: []\n---\n", encoding="utf-8")

        await ingest_file(src)
        v1 = await _get_data_version(ingest_env)

        await ingest_file(src)  # same file, skip
        v2 = await _get_data_version(ingest_env)

        assert v2 == v1, (
            f"data_version must not increment on a skipped ingest (AC-F16dv-4); "
            f"was {v1}, got {v2}"
        )


# ── AC-WATCH-3: modified file updates in-place ────────────────────────────────


class TestModifiedFileUpsert:
    """T-INC-007, T-INC-008 — AC-WATCH-3"""

    async def test_modified_file_updates_row_in_place(self, ingest_env: dict[str, Any]) -> None:
        """T-INC-007: Modify content → updated_at changes, content_hash changes, still 1 row."""
        from app.ingest.orchestrator import ingest_file

        src = ingest_env["sources_dir"] / "modified.md"
        src.write_text("---\ntype: entity\ntitle: Before\nsources: []\n---\n", encoding="utf-8")
        rel = str(src.relative_to(ingest_env["vault_root"]))

        r1 = await ingest_file(src)
        row_before = await _get_row(ingest_env, rel)
        assert row_before is not None
        hash_before = row_before["content_hash"]

        # Modify content (new mtime, new hash)
        time.sleep(0.01)  # ensure different mtime
        src.write_text(
            "---\ntype: entity\ntitle: After\nsources: []\n---\n\nNew content.\n",
            encoding="utf-8",
        )

        r2 = await ingest_file(src)
        row_after = await _get_row(ingest_env, rel)
        assert row_after is not None

        assert (
            row_after["content_hash"] != hash_before
        ), "content_hash must change after file modification"
        assert (
            r2.page_id == r1.page_id
        ), "page_id must be stable across modify (upsert, not new row)"
        assert (
            await _row_count(ingest_env, rel) == 1
        ), "Must still be exactly 1 live row after modification"

    async def test_modified_file_replaces_qdrant_point(self, ingest_env: dict[str, Any]) -> None:
        """T-INC-008: Modify → Qdrant upsert replaces the point (no orphan)."""
        from app.ingest.orchestrator import ingest_file

        src = ingest_env["sources_dir"] / "qdrant_update.md"
        src.write_text("---\ntype: entity\ntitle: Old\nsources: []\n---\n", encoding="utf-8")

        r1 = await ingest_file(src)
        upserts_after_first = len(ingest_env["qdrant"].upsert_calls)

        time.sleep(0.01)
        src.write_text(
            "---\ntype: entity\ntitle: New\nsources: []\n---\n\nUpdated.\n",
            encoding="utf-8",
        )
        await ingest_file(src)
        upserts_after_second = len(ingest_env["qdrant"].upsert_calls)

        # Second ingest must call upsert again (new embedding)
        assert (
            upserts_after_second > upserts_after_first
        ), "Modified file must trigger a new Qdrant upsert"
        # Still exactly 1 point per UUID
        assert ingest_env["qdrant"].point_exists(
            r1.page_id
        ), "Qdrant point must still exist after update"


# ── AC-WATCH-4: deleted file → soft-delete ───────────────────────────────────


class TestFileDelete:
    """T-INC-009, T-INC-010 — AC-WATCH-4"""

    async def test_delete_file_sets_deleted_at(self, ingest_env: dict[str, Any]) -> None:
        """T-INC-009: delete_file() sets deleted_at to non-NULL."""
        from app.ingest.orchestrator import delete_file, ingest_file

        src = ingest_env["sources_dir"] / "to_delete.md"
        src.write_text("---\ntype: entity\ntitle: Del\nsources: []\n---\n", encoding="utf-8")
        rel = str(src.relative_to(ingest_env["vault_root"]))

        await ingest_file(src)
        assert await _row_count(ingest_env, rel) == 1

        await delete_file(src)

        # Live row count must be 0 (deleted_at IS NOT NULL)
        assert (
            await _row_count(ingest_env, rel) == 0
        ), "After delete, live row count must be 0 (deleted_at set)"

        # Confirm deleted_at IS NOT NULL by querying with no filter
        async with ingest_env["session_factory"]() as session:
            result = await session.execute(
                sa_text("SELECT deleted_at FROM pages WHERE file_path = :fp"),
                {"fp": rel},
            )
            row = result.mappings().one_or_none()
            assert row is not None
            assert (
                row["deleted_at"] is not None
            ), "deleted_at must be set (non-NULL) after soft delete"

    async def test_delete_file_removes_qdrant_point(self, ingest_env: dict[str, Any]) -> None:
        """T-INC-010: delete_file() hard-removes the Qdrant point."""
        from app.ingest.orchestrator import delete_file, ingest_file

        src = ingest_env["sources_dir"] / "qdrant_delete.md"
        src.write_text("---\ntype: entity\ntitle: Qdel\nsources: []\n---\n", encoding="utf-8")

        r = await ingest_file(src)
        assert ingest_env["qdrant"].point_exists(r.page_id), "Point must exist before delete"

        await delete_file(src)

        assert not ingest_env["qdrant"].point_exists(
            r.page_id
        ), "Qdrant point must be hard-deleted after file deletion (AC-WATCH-4)"


# ── AC-K4-1: log line format ───────────────────────────────────────────────────


class TestLogMdAppend:
    """T-INC-011, T-INC-012, T-INC-013 — AC-K4-1, AC-K4-2"""

    async def test_one_log_line_per_ingest_with_correct_format(
        self, ingest_env: dict[str, Any]
    ) -> None:
        """T-INC-011: AC-K4-1 — each ingest appends exactly one INDEXED log line."""
        from app.ingest.orchestrator import ingest_file

        src = ingest_env["sources_dir"] / "log_test.md"
        src.write_text("---\ntype: entity\ntitle: Log\nsources: []\n---\n", encoding="utf-8")

        lines_before = len(_log_lines(ingest_env))
        await ingest_file(src)
        lines_after = _log_lines(ingest_env)

        assert len(lines_after) == lines_before + 1, (
            f"log.md must grow by exactly 1 line per ingest; "
            f"before={lines_before}, after={len(lines_after)}"
        )
        new_line = lines_after[-1]
        assert LOG_LINE_PATTERN.match(new_line), (
            f"Log line format must match YYYY-MM-DDTHH:MM:SSZ | INDEXED | <path>; "
            f"got: {new_line!r}"
        )

    async def test_three_ingests_three_log_lines(self, ingest_env: dict[str, Any]) -> None:
        """T-INC-012: AC-K4-2 — after 3 ingests log.md has exactly 3 INDEXED lines."""
        from app.ingest.orchestrator import ingest_file

        lines_before = len(_log_lines(ingest_env))
        for i in range(3):
            src = ingest_env["sources_dir"] / f"multi_{i}.md"
            src.write_text(
                f"---\ntype: entity\ntitle: Item{i}\nsources: []\n---\n",
                encoding="utf-8",
            )
            await ingest_file(src)

        lines_after = len(_log_lines(ingest_env))
        assert lines_after == lines_before + 3, (
            f"After 3 ingests log must have {lines_before + 3} lines; " f"got {lines_after}"
        )

    async def test_log_md_never_shrinks(self, ingest_env: dict[str, Any]) -> None:
        """T-INC-013: AC-K4-2 — log.md line count never decreases across operations."""
        from app.ingest.orchestrator import delete_file, ingest_file

        src1 = ingest_env["sources_dir"] / "shrink1.md"
        src1.write_text("---\ntype: entity\ntitle: S1\nsources: []\n---\n", encoding="utf-8")
        src2 = ingest_env["sources_dir"] / "shrink2.md"
        src2.write_text("---\ntype: entity\ntitle: S2\nsources: []\n---\n", encoding="utf-8")

        await ingest_file(src1)
        count_1 = len(_log_lines(ingest_env))

        await ingest_file(src2)
        count_2 = len(_log_lines(ingest_env))

        # Delete src1 — must not affect log.md (no log write on delete)
        await delete_file(src1)
        count_3 = len(_log_lines(ingest_env))

        assert count_2 >= count_1, "log.md must not shrink after second ingest"
        assert count_3 >= count_2, "log.md must not shrink after file deletion"


# ── AC-F16dv-2: data_version increments ──────────────────────────────────────


class TestDataVersionMonotonicity:
    """T-INC-014, T-INC-015 — AC-F16dv-2, AC-F16dv-4"""

    async def test_data_version_increments_on_each_new_ingest(
        self, ingest_env: dict[str, Any]
    ) -> None:
        """T-INC-014: AC-F16dv-2 — data_version bumps +1 on each successful ingest."""
        from app.ingest.orchestrator import ingest_file

        v0 = await _get_data_version(ingest_env)

        src1 = ingest_env["sources_dir"] / "dv1.md"
        src1.write_text("---\ntype: entity\ntitle: DV1\nsources: []\n---\n", encoding="utf-8")
        await ingest_file(src1)
        v1 = await _get_data_version(ingest_env)
        assert v1 == v0 + 1, f"data_version should be {v0 + 1}, got {v1}"

        src2 = ingest_env["sources_dir"] / "dv2.md"
        src2.write_text("---\ntype: entity\ntitle: DV2\nsources: []\n---\n", encoding="utf-8")
        await ingest_file(src2)
        v2 = await _get_data_version(ingest_env)
        assert v2 == v0 + 2, f"data_version should be {v0 + 2}, got {v2}"

    async def test_data_version_not_decremented_by_delete(self, ingest_env: dict[str, Any]) -> None:
        """T-INC-015: AC-F16dv-4 — delete does not decrement data_version."""
        from app.ingest.orchestrator import delete_file, ingest_file

        src = ingest_env["sources_dir"] / "dv_del.md"
        src.write_text("---\ntype: entity\ntitle: DVDel\nsources: []\n---\n", encoding="utf-8")

        await ingest_file(src)
        v_after_ingest = await _get_data_version(ingest_env)

        await delete_file(src)
        v_after_delete = await _get_data_version(ingest_env)

        assert v_after_delete >= v_after_ingest, (
            f"data_version must not decrease after file deletion (AC-F16dv-4); "
            f"was {v_after_ingest}, got {v_after_delete}"
        )


# ── AC-WATCH-5: no startup rescan (structural test) ──────────────────────────


class TestNoStartupRescan:
    """T-INC-016 — AC-WATCH-5, I1"""

    def test_watcher_code_does_not_enumerate_directory_on_start(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        T-INC-016: AC-WATCH-5, I1 — VaultWatcher.start() must not programmatically
        enumerate the directory and call ingest_file() on existing files.

        Strategy: patch ingest_file at module level; write the file with sufficient
        delay before observer start so macOS FSEvents coalesces the write event before
        the observer registers (FSEvents delivers buffered events for very-recent writes
        after observer start — this is OS-level behavior, not a watcher code defect).

        The structural guarantee (no os.walk/rglob in watcher.py) is tested by
        test_vault_structure.py::TestNoDirectoryWalk.

        This test verifies that when a file genuinely pre-existed (written > 1s ago),
        no ingest_file() call is made on startup.
        """
        import asyncio
        import time

        from app import config as cfg
        from app.watcher import VaultWatcher

        # Set up a temp sources dir with a pre-existing file
        sources_dir = tmp_path / "raw" / "sources"
        sources_dir.mkdir(parents=True)
        (sources_dir / "pre_existing.md").write_text("# Pre-existing\n", encoding="utf-8")

        # Wait 1.5s so macOS FSEvents coalesces the write event from before observer start.
        # This simulates the real-world case: files exist hours/days before service restart.
        time.sleep(1.5)

        monkeypatch.setattr(cfg.settings, "vault_path", str(tmp_path))
        monkeypatch.setattr(type(cfg.settings), "vault_root", property(lambda self: tmp_path))
        monkeypatch.setattr(
            type(cfg.settings), "raw_sources_dir", property(lambda self: sources_dir)
        )

        ingest_calls: list[str] = []

        async def fake_ingest(path: object) -> None:
            ingest_calls.append(str(path))

        monkeypatch.setattr("app.ingest.orchestrator.ingest_file", fake_ingest)

        loop = asyncio.new_event_loop()
        watcher = VaultWatcher()
        try:
            watcher.start(loop)
            # Let any spurious events drain
            loop.run_until_complete(asyncio.sleep(0.3))
        finally:
            watcher.stop()
            loop.close()

        assert ingest_calls == [], (
            f"VaultWatcher.start() must not call ingest_file() on startup for pre-existing "
            f"files (AC-WATCH-5, I1). "
            f"Called for: {ingest_calls}\n"
            "If this fails: check watcher.py for os.walk/rglob directory enumeration "
            "or FSEvents delivery of very-recent writes (use a longer pre-existing delay)."
        )

    def test_watcher_has_no_directory_walk_code(self) -> None:
        """
        T-INC-017: Static assertion that watcher.py contains no directory enumeration code.

        This is the primary I1/AC-WATCH-5 guard. The OS-event-delivery question is
        separate (FSEvents coalescing) — the code must never walk the directory.
        """
        watcher_path = Path(__file__).resolve().parent.parent / "app" / "watcher.py"
        # Read only executable lines (exclude comments/docstrings)
        lines = watcher_path.read_text(encoding="utf-8").splitlines()
        code_lines = []
        in_docstring = False
        for line in lines:
            stripped = line.strip()
            if not in_docstring and (stripped.startswith('"""') or stripped.startswith("'''")):
                count = stripped.count('"""') + stripped.count("'''")
                if count % 2 != 0:
                    in_docstring = True
                    continue
                continue
            if in_docstring:
                if '"""' in stripped or "'''" in stripped:
                    in_docstring = False
                continue
            if not stripped.startswith("#"):
                code_lines.append(line)

        code_text = "\n".join(code_lines)
        forbidden = ["os.listdir", "os.walk", ".rglob(", "glob.glob"]
        for pattern in forbidden:
            assert pattern not in code_text, (
                f"watcher.py executable code contains directory enumeration "
                f"pattern {pattern!r} — violates I1/AC-WATCH-5"
            )
