"""
Priority-1 QA gate test: PUT /pages/{id}/content must NOT trigger the LLM ingest pipeline.

The risk (as of this sprint): PUT /pages/{id}/content calls ingest_file(wiki_path).
ingest_file is the same primitive used for raw/sources/ ingestion. When a
provider_config IS configured for the vault, ingest_file calls
_resolve_ingest_provider_config() → run_ingest_pipeline() → provider.analyze() +
provider.generate() — potentially overwriting the user's manually-edited content
with LLM-regenerated content (correctness bug / data-loss risk).

This test asserts:
  (a) T-PC-P1a: PUT on an existing wiki page persists the user's EXACT edited bytes
      even when a provider is configured (no LLM regeneration, no content alteration).
  (b) T-PC-P1b: PUT does NOT invoke provider.generate() (and therefore does not
      create a new ingest_runs row with status="completed" for the wiki edit path).
  (c) T-PC-P1c: The inline re-index from ingest_file updates only metadata + Qdrant
      embedding — it does NOT re-run analyze/generate on the wiki content.

CONCERN DOCUMENTATION (ADR-0035 gap):
  If the caller of ingest_file() is PUT /pages/{id}/content, and a provider config is
  active, the current code (orchestrator.py:161-168) will run the full LLM pipeline on
  the wiki/ file text, treating the user's markdown as if it were a raw source document.

  The purpose-built primitive for wiki in-place edits is reindex_wiki_page_body()
  (orchestrator.py:743-819), which: updates content_hash, re-embeds into Qdrant,
  re-derives wikilinks, and bumps data_version — without calling analyze/generate.

  RECOMMENDED FIX: PUT /pages/{id}/content should call reindex_wiki_page_body()
  directly instead of ingest_file(). This is a one-line swap in main.py:1739.

This test guards against the regression by:
  1. Patching _resolve_ingest_provider_config to return a fake config (simulating
     a real provider being configured).
  2. Patching run_ingest_pipeline to raise an AssertionError if called (guards
     against the bug triggering silently).
  3. Calling PUT /pages/{id}/content.
  4. Asserting the response is 200 and the on-disk content equals the sent body.

If the current code calls run_ingest_pipeline, the test will FAIL — which is the
correct outcome when the bug is present. A green result means the endpoint is safe.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient

# Re-use shared fixtures
from tests.test_api import api_client, api_env  # noqa: F401


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def _ingest_wiki_page(
    api_env: dict[str, Any],
    *,
    filename: str,
    content: str,
) -> tuple[str, Path]:
    """
    Write a file to vault/wiki/entities/ and ingest it.
    Returns (page_id_str, abs_path).
    """
    from app.ingest.orchestrator import ingest_file

    wiki_entities = api_env["vault_root"] / "wiki" / "entities"
    wiki_entities.mkdir(parents=True, exist_ok=True)
    wiki_file = wiki_entities / filename
    wiki_file.write_text(content, encoding="utf-8")
    result = await ingest_file(wiki_file)
    return str(result.page_id), wiki_file


# ── T-PC-P1a/b/c: PUT must not trigger the LLM pipeline ─────────────────────


class TestPutPageContentNoLLM:
    """
    Priority-1 load-bearing acceptance test (ADR-0035 gap).

    Scenario: vault has an active provider_config (normal in production).
    PUT /pages/{id}/content should edit the wiki page without ever calling the
    provider analyze/generate pipeline.
    """

    async def test_put_preserves_exact_content_with_active_provider(
        self,
        api_client: AsyncClient,
        api_env: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        T-PC-P1a: PUT writes user's EXACT bytes. Content is NOT regenerated/altered
        by the LLM pipeline even when a provider_config row is active.

        Mechanism: we stub _resolve_ingest_provider_config to return a fake config
        (imitating production with a configured provider), then stub run_ingest_pipeline
        to raise AssertionError if called. The test passes only if ingest_file's
        provider branch is never entered for a wiki/ path, OR the endpoint bypasses
        ingest_file entirely.

        If this test FAILS with AssertionError raised by run_ingest_pipeline_guard,
        the bug is CONFIRMED: PUT /pages/{id}/content is invoking the LLM pipeline.
        Recommended fix: replace ingest_file(abs_path) with reindex_wiki_page_body()
        at main.py:1739 (orchestrator.py already exports the helper).
        """
        import app.ingest.orchestrator as orch

        original_content = (
            "---\ntype: entity\ntitle: Test Entity\nsources: []\n---\n\nOriginal body.\n"
        )
        page_id, wiki_file = await _ingest_wiki_page(
            api_env,
            filename="llm_guard_test.md",
            content=original_content,
        )

        # Simulate a configured provider (as in production)
        fake_provider_config = MagicMock()
        fake_provider_config.provider_type = "local"
        fake_provider_config.model_id = "fake-model"
        fake_provider_config.max_iter = 3
        fake_provider_config.token_budget = 4096

        async def fake_resolve_provider_config() -> object:
            return fake_provider_config

        monkeypatch.setattr(
            orch,
            "_resolve_ingest_provider_config",
            fake_resolve_provider_config,
        )

        # Guard: if run_ingest_pipeline is called, the bug is triggered.
        pipeline_called: list[bool] = []

        async def run_ingest_pipeline_guard(**kwargs: Any) -> None:  # type: ignore[return]
            pipeline_called.append(True)
            raise AssertionError(
                "BUG CONFIRMED (ADR-0035 gap): PUT /pages/{id}/content called "
                "run_ingest_pipeline on a wiki/ file. This will regenerate the user's "
                "manually-edited content via the LLM analyze→generate loop. "
                "FIX: replace ingest_file(abs_path) at main.py:1739 with "
                "reindex_wiki_page_body(page=..., new_file_text=..., "
                "body_for_embedding=..., bump=True)."
            )

        monkeypatch.setattr(orch, "run_ingest_pipeline", run_ingest_pipeline_guard)

        # Perform the PUT
        new_content = "---\ntype: entity\ntitle: Test Entity\nsources: []\n---\n\nEdited by user.\n"
        resp = await api_client.put(
            f"/pages/{page_id}/content",
            json={"content": new_content},
        )

        # Assertion (a): response must be 200
        assert resp.status_code == 200, (
            f"PUT returned {resp.status_code}: {resp.text}\n"
            "If AssertionError was raised internally, the LLM pipeline was triggered."
        )

        # Assertion (a): on-disk content equals what we sent (no LLM alteration)
        on_disk = wiki_file.read_text(encoding="utf-8")
        assert on_disk == new_content, (
            f"On-disk content was altered by the re-index path.\n"
            f"Expected: {new_content!r}\n"
            f"Got:      {on_disk!r}"
        )

        # Assertion (b): the LLM pipeline was NEVER called
        assert not pipeline_called, (
            "BUG: run_ingest_pipeline was called for a wiki/ PUT edit. "
            "The user's content would be overwritten by LLM generation. "
            "Fix: use reindex_wiki_page_body() in PUT /pages/{id}/content (main.py:1739)."
        )

    async def test_put_hash_updated_in_db_without_provider_call(
        self,
        api_client: AsyncClient,
        api_env: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        T-PC-P1c: The inline re-index from reindex_wiki_page_body updates content_hash
        in Postgres without invoking provider.generate().

        Verifies that after PUT, the DB row has the hash of the NEW content
        (not the old hash), proving the metadata update path ran correctly
        and independently of any LLM call.
        """
        import app.ingest.orchestrator as orch
        from app.ingest.orchestrator import _load_page

        # Set up fixture page FIRST (before patching) so ingest_file runs clean.
        old_content = "---\ntype: concept\ntitle: Hash Check\nsources: []\n---\n\nOld content.\n"
        page_id, wiki_file = await _ingest_wiki_page(
            api_env,
            filename="hash_check.md",
            content=old_content,
        )

        # AFTER fixture setup: patch provider + guard the pipeline for the PUT call only.
        fake_provider_config = MagicMock()

        async def fake_resolve_provider_config() -> object:
            return fake_provider_config

        monkeypatch.setattr(
            orch,
            "_resolve_ingest_provider_config",
            fake_resolve_provider_config,
        )

        generate_calls: list[str] = []

        async def pipeline_spy(**kwargs: Any) -> None:  # type: ignore[return]
            generate_calls.append(str(kwargs.get("origin_source", "?")))
            raise AssertionError(
                "run_ingest_pipeline must NOT be called by PUT /pages/{id}/content."
            )

        monkeypatch.setattr(orch, "run_ingest_pipeline", pipeline_spy)

        new_content = "---\ntype: concept\ntitle: Hash Check\nsources: []\n---\n\nNew content.\n"
        resp = await api_client.put(
            f"/pages/{page_id}/content",
            json={"content": new_content},
        )
        assert resp.status_code == 200, f"PUT returned {resp.status_code}: {resp.text}"

        # DB row must carry the NEW content hash
        rel_path = str(wiki_file.resolve().relative_to(api_env["vault_root"].resolve()))
        db_page = await _load_page(rel_path)
        assert db_page is not None, "Page row must exist after PUT"

        expected_hash = _sha256(new_content.encode("utf-8"))
        assert db_page.content_hash == expected_hash, (
            f"DB row content_hash not updated to the new content hash.\n"
            f"Expected: {expected_hash!r}\n"
            f"Got:      {db_page.content_hash!r}\n"
            "The re-index step must update content_hash without an LLM call."
        )

        # The pipeline must not have been triggered
        assert not generate_calls, (
            f"LLM pipeline was called for: {generate_calls}. "
            "PUT /pages/{{id}}/content must not trigger analyze/generate."
        )

    async def test_put_no_new_ingest_run_row_created(
        self,
        api_client: AsyncClient,
        api_env: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        T-PC-P1b: PUT /pages/{id}/content must NOT create a new ingest_runs row.

        An ingest_runs row is only created by run_ingest_pipeline (via _write_ingest_run).
        If a row IS created by PUT, the LLM pipeline was invoked — data loss risk.

        We verify this by counting _write_ingest_run calls: it must be 0 for a wiki PUT.
        """
        import app.ingest.orchestrator as orch

        # Set up fixture page FIRST (before patching).
        old_content = "---\ntype: entity\ntitle: Run Check\nsources: []\n---\n\nOld.\n"
        page_id, wiki_file = await _ingest_wiki_page(
            api_env,
            filename="run_check.md",
            content=old_content,
        )

        # AFTER fixture: install spies and guards for the PUT call only.
        write_run_calls: list[dict[str, Any]] = []
        original_write_run = orch._write_ingest_run  # type: ignore[attr-defined]

        async def spy_write_run(**kwargs: Any) -> None:
            write_run_calls.append(kwargs)
            await original_write_run(**kwargs)

        monkeypatch.setattr(orch, "_write_ingest_run", spy_write_run)

        # Provider configured — simulates production
        fake_config = MagicMock()

        async def fake_resolve() -> object:
            return fake_config

        monkeypatch.setattr(orch, "_resolve_ingest_provider_config", fake_resolve)

        # Guard the pipeline — if called, the bug is present
        async def pipeline_trap(**kwargs: Any) -> None:  # type: ignore[return]
            raise AssertionError("run_ingest_pipeline must not be called on wiki/ PUT")

        monkeypatch.setattr(orch, "run_ingest_pipeline", pipeline_trap)

        new_content = "---\ntype: entity\ntitle: Run Check\nsources: []\n---\n\nEdited.\n"
        resp = await api_client.put(
            f"/pages/{page_id}/content",
            json={"content": new_content},
        )
        assert resp.status_code == 200, f"PUT returned {resp.status_code}: {resp.text}"

        assert not write_run_calls, (
            f"BUG: PUT /pages/{{id}}/content created {len(write_run_calls)} ingest_runs "
            f"row(s): {write_run_calls}. "
            "A wiki page edit must NOT write an ingest_runs record. "
            "Fix: use reindex_wiki_page_body() instead of ingest_file() at main.py:1739."
        )
