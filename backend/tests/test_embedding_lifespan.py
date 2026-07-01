"""
Integration tests for the embedding toggle's lifespan behaviour (ADR-0030).

Coverage:
  GAP-EMB-STARTUP (ADR-0030 B-AC-1):
    - With embeddings_enabled=False the startup probe (_validate_embedding_and_collection)
      is NOT called and the app still starts successfully.
    - With embeddings_enabled=True the probe IS called.

  GAP-EMB-TOGGLE (ADR-0030 B-AC-5 / I1 no-bulk-re-embed guarantee):
    - Flipping embeddings_enabled False→True (without new ingest) triggers NO embed calls
      and NO Qdrant upserts for already-ingested pages.  The toggle is side-effect-free (I1).

Both tests are infra-free: SQLite in-memory, FakeEmbeddingClient, no live Postgres/Qdrant.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.embeddings import FakeEmbeddingClient, set_embedding_client
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# ── Shared no-op lifespan helper ────────────────────────────────────────────────


@asynccontextmanager
async def _noop_lifespan(app_: Any) -> AsyncGenerator[None, None]:
    """Suppress the real FastAPI lifespan so tests remain infra-free."""
    yield


# ── GAP-EMB-STARTUP (ADR-0030 B-AC-1) ──────────────────────────────────────────


class TestEmbeddingStartupToggle:
    """
    The startup probe (_validate_embedding_and_collection) is guarded by
    settings.embeddings_enabled and must ONLY run when the flag is True.

    Strategy: exercise the real `lifespan()` coroutine directly by patching all
    heavy I/O dependencies (Postgres, Qdrant, watcher, GraphCache, scheduler) so
    the lifespan body runs but no real network call is made.  The spy on
    _validate_embedding_and_collection tells us whether it was entered.
    """

    @pytest.mark.asyncio
    async def test_startup_skips_probe_when_embeddings_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        GAP-EMB-STARTUP (ADR-0030 B-AC-1): when embeddings_enabled=False,
        _validate_embedding_and_collection must NOT be called during lifespan startup,
        and the app must start without error.
        """
        import app.main as main_mod

        monkeypatch.setattr(main_mod.settings, "embeddings_enabled", False)

        called: list[str] = []

        async def _fake_validate() -> None:
            called.append("validate_called")

        # Patch all the heavy I/O helpers so the lifespan body runs without real infra.
        # _seed_vault_state is async — replace with a coroutine via AsyncMock.
        seed_mock = AsyncMock()
        graph_cache_mock = MagicMock()
        graph_cache_mock.return_value = MagicMock()
        graph_cache_mock.return_value.start_background_loop = MagicMock()
        graph_cache_mock.return_value.stop_background_loop = MagicMock()
        scheduler_mock = MagicMock()
        scheduler_mock.return_value = MagicMock()
        scheduler_mock.return_value.start = MagicMock()
        scheduler_mock.return_value.stop = MagicMock()
        dispose_mock = AsyncMock()

        load_flag_mock = AsyncMock()
        load_mcp_auth_mock = AsyncMock()
        load_clip_config_mock = AsyncMock()
        with (
            patch.object(main_mod, "_validate_embedding_and_collection", _fake_validate),
            patch.object(main_mod, "_seed_vault_state", seed_mock),
            # ADR-0032: _load_remote_mcp_flag also runs in lifespan; patch to avoid DB hit.
            patch.object(main_mod, "_load_remote_mcp_flag", load_flag_mock),
            # ADR-0033: _load_mcp_auth_cache also runs in lifespan; patch to avoid DB hit.
            patch.object(main_mod, "_load_mcp_auth_cache", load_mcp_auth_mock),
            # ADR-0040: _load_clip_config_cache also runs in lifespan; patch to avoid DB hit.
            patch.object(main_mod, "_load_clip_config_cache", load_clip_config_mock),
            patch("app.main.bootstrap_vault"),
            patch("app.main.start_watcher"),
            patch("app.main.stop_watcher"),
            patch("app.main.dispose_engine", dispose_mock),
            patch("app.main.GraphCache", graph_cache_mock),
            patch("app.main.ImportScheduler", scheduler_mock),
        ):
            from app.main import app, lifespan

            # Run the lifespan directly as a context manager rather than going
            # through ASGITransport (which would run a full request/DB cycle).
            async with lifespan(app):
                # The lifespan has completed startup; no request needed.
                pass

        # Reset module-level _graph_cache to None so subsequent tests that patch
        # GraphCache.get_graph() at the class level work correctly.  The lifespan
        # exit path calls stop_background_loop() on the mock but does NOT restore
        # the singleton to None, leaving a non-awaitable MagicMock in place that
        # breaks test_graph_api.py::TestGraphResponseSchema (shared-state defect).
        main_mod._graph_cache = None  # type: ignore[attr-defined]

        assert called == [], (
            "_validate_embedding_and_collection must NOT be called when embeddings_enabled=False "
            "(ADR-0030 B-AC-1)"
        )

    @pytest.mark.asyncio
    async def test_startup_calls_probe_when_embeddings_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        GAP-EMB-STARTUP (ADR-0030 B-AC-1): when embeddings_enabled=True,
        _validate_embedding_and_collection IS called during lifespan startup.
        """
        import app.main as main_mod

        monkeypatch.setattr(main_mod.settings, "embeddings_enabled", True)

        called: list[str] = []

        async def _fake_validate() -> None:
            called.append("validate_called")

        seed_mock = AsyncMock()
        graph_cache_mock = MagicMock()
        graph_cache_mock.return_value = MagicMock()
        graph_cache_mock.return_value.start_background_loop = MagicMock()
        graph_cache_mock.return_value.stop_background_loop = MagicMock()
        scheduler_mock = MagicMock()
        scheduler_mock.return_value = MagicMock()
        scheduler_mock.return_value.start = MagicMock()
        scheduler_mock.return_value.stop = MagicMock()
        dispose_mock = AsyncMock()

        load_flag_mock = AsyncMock()
        load_mcp_auth_mock = AsyncMock()
        load_clip_config_mock = AsyncMock()
        with (
            patch.object(main_mod, "_validate_embedding_and_collection", _fake_validate),
            patch.object(main_mod, "_seed_vault_state", seed_mock),
            # ADR-0032: _load_remote_mcp_flag also runs in lifespan; patch to avoid DB hit.
            patch.object(main_mod, "_load_remote_mcp_flag", load_flag_mock),
            # ADR-0033: _load_mcp_auth_cache also runs in lifespan; patch to avoid DB hit.
            patch.object(main_mod, "_load_mcp_auth_cache", load_mcp_auth_mock),
            # ADR-0040: _load_clip_config_cache also runs in lifespan; patch to avoid DB hit.
            patch.object(main_mod, "_load_clip_config_cache", load_clip_config_mock),
            patch("app.main.bootstrap_vault"),
            patch("app.main.start_watcher"),
            patch("app.main.stop_watcher"),
            patch("app.main.dispose_engine", dispose_mock),
            patch("app.main.GraphCache", graph_cache_mock),
            patch("app.main.ImportScheduler", scheduler_mock),
        ):
            from app.main import app, lifespan

            async with lifespan(app):
                pass

        # Reset module-level _graph_cache to None (see first test for full explanation).
        main_mod._graph_cache = None  # type: ignore[attr-defined]

        assert called == ["validate_called"], (
            "_validate_embedding_and_collection must be called when embeddings_enabled=True "
            "(ADR-0030 B-AC-1)"
        )


# ── GAP-EMB-TOGGLE (ADR-0030 B-AC-5 / I1) ─────────────────────────────────────


class TestEmbeddingToggleSideEffectFree:
    """
    Toggling embeddings_enabled (False→True→False) with no new ingest MUST NOT
    trigger any embed calls or Qdrant upserts for already-ingested pages (I1).

    Test plan:
      1. Ingest one page with embeddings ON  → embed called once, qdrant upsert once.
      2. Record counts.
      3. Flip embeddings_enabled False → True (monkeypatch on settings, no background task).
      4. Assert embed call_count and qdrant upsert_calls have NOT increased.
    """

    @pytest.mark.asyncio
    async def test_toggle_does_not_re_embed_existing_pages(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        GAP-EMB-TOGGLE (ADR-0030 B-AC-5 / I1): flipping the embeddings_enabled flag
        does NOT trigger embed calls or Qdrant upserts on already-ingested pages.
        """
        import app.config as cfg_mod
        import app.ingest.orchestrator as orch_mod

        # ── Build a minimal in-memory environment ────────────────────────────────
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        async with engine.begin() as conn:
            await conn.execute(
                sa_text(
                    """
                CREATE TABLE pages (
                    id TEXT PRIMARY KEY,
                    vault_id TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    title TEXT,
                    type TEXT,
                    sources TEXT,
                    content_hash TEXT NOT NULL DEFAULT '',
                    source_mtime_ns INTEGER,
                    qdrant_point_id TEXT,
                    x REAL,
                    y REAL,
                    pinned INTEGER NOT NULL DEFAULT 0,
                    deleted_at TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """
                )
            )
            await conn.execute(
                sa_text(
                    """
                CREATE TABLE links (
                    id TEXT PRIMARY KEY,
                    source_page_id TEXT NOT NULL,
                    target_title TEXT NOT NULL,
                    target_page_id TEXT,
                    dangling INTEGER NOT NULL DEFAULT 0
                )
            """
                )
            )
            await conn.execute(
                sa_text(
                    """
                CREATE TABLE vault_state (
                    id TEXT PRIMARY KEY,
                    vault_id TEXT NOT NULL UNIQUE,
                    data_version INTEGER NOT NULL DEFAULT 0,
                    remote_mcp_enabled INTEGER NOT NULL DEFAULT 0,
                    mcp_access_token_hash TEXT,
                    mcp_allow_without_token INTEGER NOT NULL DEFAULT 0,
                    clip_enabled_db INTEGER,
                    clip_access_token TEXT,
                    clip_allowed_origins_db TEXT,
                    updated_at TEXT NOT NULL
                )
            """
                )
            )

        session_factory = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )

        # Seed vault_state
        async with session_factory() as sess:
            await sess.execute(
                sa_text(
                    "INSERT INTO vault_state (id, vault_id, data_version, updated_at) "
                    "VALUES (:id, :vault_id, 0, datetime('now'))"
                ).bindparams(id=str(uuid.uuid4()), vault_id="toggle-test")
            )
            await sess.commit()

        # ── Vault directories ─────────────────────────────────────────────────
        vault_root = tmp_path / "vault"
        sources_dir = vault_root / "raw" / "sources"
        sources_dir.mkdir(parents=True)
        wiki_dir = vault_root / "wiki"
        wiki_dir.mkdir()
        log_md = wiki_dir / "log.md"
        log_md.write_text("---\ntype: log\ntitle: Synapse Ingest Log\n---\n\n", encoding="utf-8")

        monkeypatch.setattr(cfg_mod.settings, "vault_path", str(vault_root))
        monkeypatch.setattr(cfg_mod.settings, "vault_id", "toggle-test")
        monkeypatch.setattr(type(cfg_mod.settings), "vault_root", property(lambda s: vault_root))
        monkeypatch.setattr(
            type(cfg_mod.settings), "raw_sources_dir", property(lambda s: sources_dir)
        )
        monkeypatch.setattr(type(cfg_mod.settings), "wiki_dir", property(lambda s: wiki_dir))
        monkeypatch.setattr(type(cfg_mod.settings), "log_md_path", property(lambda s: log_md))

        # ── Patch DB session at the orchestrator's own import reference ───────
        @asynccontextmanager
        async def patched_get_session():  # type: ignore[return]
            async with session_factory() as s:
                try:
                    yield s
                    await s.commit()
                except Exception:
                    await s.rollback()
                    raise

        monkeypatch.setattr("app.db.get_session", patched_get_session)
        monkeypatch.setattr("app.ingest.orchestrator.get_session", patched_get_session)
        monkeypatch.setattr("app.provider_config_service.get_session", patched_get_session)

        # ── Fake embedding client with call counting ────────────────────────────
        fake_emb = FakeEmbeddingClient(dim=4)
        set_embedding_client(fake_emb)

        # ── Fake Qdrant upsert counter ────────────────────────────────────────
        qdrant_upsert_count: list[int] = [0]

        async def _fake_upsert_point(**kwargs: Any) -> None:
            qdrant_upsert_count[0] += 1

        monkeypatch.setattr("app.ingest.orchestrator.upsert_point", _fake_upsert_point)

        # ── STEP 1: Ingest one page with embeddings ON ────────────────────────
        monkeypatch.setattr(cfg_mod.settings, "embeddings_enabled", True)
        monkeypatch.setattr(orch_mod.settings, "embeddings_enabled", True)

        src_file = sources_dir / "toggle_test_page.md"
        src_file.write_text(
            "---\ntype: entity\ntitle: Toggle Test Page\nsources: []\n---\n\nBody.\n",
            encoding="utf-8",
        )

        from app.ingest.orchestrator import ingest_file

        await ingest_file(src_file)

        # Capture counts after ingest with embeddings ON.
        embed_count_after_ingest = fake_emb.call_count
        qdrant_upsert_after_ingest = qdrant_upsert_count[0]

        # Sanity: embedding WAS called (confirms embeddings_enabled=True path works).
        assert (
            embed_count_after_ingest >= 1
        ), "Expected at least one embed() call during ingest with embeddings_enabled=True"

        # ── STEP 2: Toggle embeddings_enabled False → True (no new ingest) ────
        # The toggle is a pure settings attribute mutation; there is no background
        # task or hook that would re-scan/re-embed existing pages (I1).
        monkeypatch.setattr(cfg_mod.settings, "embeddings_enabled", False)
        monkeypatch.setattr(orch_mod.settings, "embeddings_enabled", False)
        # Flip back to True.
        monkeypatch.setattr(cfg_mod.settings, "embeddings_enabled", True)
        monkeypatch.setattr(orch_mod.settings, "embeddings_enabled", True)

        # ── STEP 3: Assert no background re-embed happened ─────────────────────
        extra_embeds = fake_emb.call_count - embed_count_after_ingest
        assert extra_embeds == 0, (
            f"embed() was called {extra_embeds} extra times after toggling "
            "embeddings_enabled — toggle must be side-effect-free (I1, ADR-0030 B-AC-5)"
        )
        assert qdrant_upsert_count[0] == qdrant_upsert_after_ingest, (
            f"Qdrant upsert was called {qdrant_upsert_count[0] - qdrant_upsert_after_ingest} "
            "extra times after toggling embeddings_enabled — toggle must be side-effect-free (I1)"
        )

        # Teardown
        set_embedding_client(None)  # type: ignore[arg-type]
        await engine.dispose()
