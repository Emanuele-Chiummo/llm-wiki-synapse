"""
Boot-vault meta-file indexing regression test (NC-3 follow-up, 2.1.3).

index_bootstrap_meta_files() (backend/app/ingest/orchestrator.py) indexes wiki/overview.md,
wiki/index.md, and wiki/log.md as Page rows so GET /pages / the NavTree "Other"/"Overview"
sections show them immediately, without waiting for the first watcher event.

Prior to 2.1.3, this was only wired into POST /projects (app/projects.py) — the BOOT vault
(settings.vault_root, bootstrapped by bootstrap_vault() in app.main's lifespan) never got the
same treatment. overview.md/index.md/log.md existed on disk but had zero Page rows until an
ingest queue-drain happened to touch overview.md (ADR-0089) — the NavTree "OVERVIEW" section
showed a permanent 0 count on a freshly-booted default vault otherwise.

This test exercises the real lifespan() coroutine (mirroring test_embedding_lifespan.py's
pattern) with all other heavy I/O patched out, and asserts index_bootstrap_meta_files is called
with the boot vault's root + vault_id.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_lifespan_indexes_boot_vault_meta_files(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.main as main_mod
    from app.ingest import orchestrator as orch_mod

    # Skip the live embedding/Qdrant startup probe (ADR-0030 B-AC-1) — infra-free test.
    monkeypatch.setattr(main_mod.settings, "embeddings_enabled", False)

    calls: list[dict[str, Any]] = []

    async def _fake_index(*, vault_root: Any, vault_id: str) -> None:
        calls.append({"vault_root": vault_root, "vault_id": vault_id})

    seed_mock = AsyncMock()
    graph_cache_mock = MagicMock()
    graph_cache_mock.return_value = MagicMock()
    graph_cache_mock.return_value.start_background_loop = MagicMock()
    graph_cache_mock.return_value.stop_background_loop = MagicMock()
    import_scheduler_mock = MagicMock()
    import_scheduler_mock.return_value = MagicMock()
    import_scheduler_mock.return_value.initialize = AsyncMock()
    import_scheduler_mock.return_value.start = MagicMock()
    import_scheduler_mock.return_value.stop = MagicMock()
    dispose_mock = AsyncMock()

    load_flag_mock = AsyncMock()
    load_mcp_auth_mock = AsyncMock()
    load_clip_config_mock = AsyncMock()
    load_web_search_config_mock = AsyncMock()
    load_cli_auth_config_mock = AsyncMock()
    load_api_token_cache_mock = AsyncMock()

    with (
        patch.object(orch_mod, "index_bootstrap_meta_files", _fake_index),
        patch.object(main_mod, "_seed_vault_state", seed_mock),
        patch.object(main_mod, "_load_remote_mcp_flag", load_flag_mock),
        patch.object(main_mod, "_load_mcp_write_flag", load_flag_mock),
        patch.object(main_mod, "_load_mcp_auth_cache", load_mcp_auth_mock),
        patch.object(main_mod, "_load_clip_config_cache", load_clip_config_mock),
        patch.object(main_mod, "_load_web_search_config_cache", load_web_search_config_mock),
        patch.object(main_mod, "_load_cli_auth_config_cache", load_cli_auth_config_mock),
        patch.object(main_mod, "_load_api_token_cache", load_api_token_cache_mock),
        patch("app.main.bootstrap_vault"),
        patch("app.main.start_watcher"),
        patch("app.main.stop_watcher"),
        patch("app.main.dispose_engine", dispose_mock),
        patch("app.main.GraphCache", graph_cache_mock),
        patch("app.main.ImportScheduler", import_scheduler_mock),
    ):
        from app.main import app, lifespan

        async with lifespan(app):
            pass

    main_mod._graph_cache = None  # type: ignore[attr-defined]
    if main_mod._ops_scheduler is not None:  # type: ignore[attr-defined]
        main_mod._ops_scheduler.stop()  # type: ignore[attr-defined]
        main_mod._ops_scheduler = None  # type: ignore[attr-defined]

    assert len(calls) == 1, f"expected exactly one boot-vault meta-index call, got {calls}"
    assert calls[0]["vault_id"] == main_mod.settings.vault_id
    assert calls[0]["vault_root"] == main_mod.settings.vault_root
