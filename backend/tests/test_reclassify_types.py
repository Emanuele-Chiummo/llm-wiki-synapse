"""
SPRINT-v1.2 tail type re-classification tests — infra-free.

Covers (mirrors test_backfill_domains.py, adapted to the single-type contract):
  * STRICT type validation: out-of-vocabulary / malformed answer → skipped, counted failed.
  * reserved types (overview/index) untouched even if they slip into the candidate list.
  * same-type proposal → skipped, NO write (idempotent).
  * caps: max_pages (stopped_reason=maxpages), token_budget (stopped_reason=budget).
  * changed pages accrue by_type + a single data_version bump for the batch.
  * single-flight state: is_running / get_last_summary exposed for the endpoint.

DB + write-back primitives are stubbed (no Postgres, no files). Provider resolution + the
vault-context loader are monkeypatched so the suite passes standalone.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from app.ops import reclassify_types as rt


def _make_provider(response: str) -> Any:
    from unittest.mock import MagicMock

    provider = MagicMock()
    provider._chat_calls = [0]

    async def mock_chat(*, messages: list[Any], retrieval_context: str = "") -> AsyncIterator[str]:
        provider._chat_calls[0] += 1

        async def _gen() -> AsyncIterator[str]:
            yield response

        return _gen()

    provider.chat = mock_chat
    provider.bind_accumulator = MagicMock()
    return provider


def _fake_page(title: str, page_type: str | None = "concept") -> Any:
    p = type("P", (), {})()
    p.id = uuid.uuid4()
    p.vault_id = "test-vault"
    p.title = title
    p.file_path = f"wiki/concepts/{title.lower().replace(' ', '-')}.md"
    p.page_type = page_type
    p.sources = ["raw/x.md"]
    p.tags = None
    p.source_mtime_ns = 0
    return p


@pytest.fixture()
def rt_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub provider, candidate query, vault-context, and the orchestrator write-back primitives."""
    import app.ingest.orchestrator as orch

    state: dict[str, Any] = {
        "pages": [],
        "applied": [],
        "bumps": 0,
        "provider": _make_provider('{"type": "entity"}'),
        "body": "page body",
    }

    async def fake_resolve(vault_id: str) -> Any:
        prov = state["provider"]
        return None if prov is None else (prov, object())

    async def fake_load(vault_id: str, max_pages: int, force: bool) -> list[Any]:
        return list(state["pages"])[:max_pages]

    async def fake_apply(page: Any, new_type: str) -> None:
        page.page_type = new_type
        state["applied"].append({"page_id": page.id, "type": new_type})

    async def fake_bump() -> None:
        state["bumps"] += 1

    def fake_read_body(page: Any) -> str:
        return state["body"]

    def fake_vault_context() -> str:
        return "# schema.md\ntype rules here"

    monkeypatch.setattr(rt, "resolve_operation_provider", fake_resolve)
    monkeypatch.setattr(rt, "_load_candidate_pages", fake_load)
    monkeypatch.setattr(orch, "apply_page_type", fake_apply)
    monkeypatch.setattr(orch, "bump_version", fake_bump)
    monkeypatch.setattr(orch, "_read_body_for_classification", fake_read_body)
    monkeypatch.setattr(orch, "_load_vault_context", fake_vault_context)

    # Reset the module-level single-flight state between tests.
    rt._state.is_running = False
    rt._state.last_summary = None
    rt._state.current = {}
    return state


async def test_reclassify_changes_all_candidates(rt_env: dict[str, Any]) -> None:
    rt_env["pages"] = [_fake_page("A"), _fake_page("B"), _fake_page("C")]
    summary = await rt.run_reclassify(vault_id="test-vault", max_pages=500)
    assert summary.processed == 3
    assert summary.changed == 3  # concept → entity for all
    assert summary.skipped == 0
    assert summary.failed == 0
    assert summary.by_type == {"entity": 3}
    assert summary.stopped_reason == "complete"
    assert rt_env["provider"]._chat_calls[0] == 3
    assert rt_env["bumps"] == 1, "one data_version bump for the whole batch"


async def test_reclassify_same_type_skipped_no_write(rt_env: dict[str, Any]) -> None:
    # Provider proposes the SAME type the page already has → no write, counted skipped.
    rt_env["provider"] = _make_provider('{"type": "concept"}')
    rt_env["pages"] = [_fake_page("A", page_type="concept")]
    summary = await rt.run_reclassify(vault_id="test-vault")
    assert summary.processed == 1
    assert summary.changed == 0
    assert summary.skipped == 1
    assert rt_env["applied"] == [], "same-type must not write back"
    assert rt_env["bumps"] == 0, "no change ⇒ no data_version bump"


async def test_reclassify_strict_validation_invalid_type(rt_env: dict[str, Any]) -> None:
    # Provider hallucinates a type outside the six → skipped, counted failed, no write.
    rt_env["provider"] = _make_provider('{"type": "nonsense"}')
    rt_env["pages"] = [_fake_page("A")]
    summary = await rt.run_reclassify(vault_id="test-vault")
    assert summary.processed == 1
    assert summary.changed == 0
    assert summary.failed == 1
    assert rt_env["applied"] == []
    assert rt_env["bumps"] == 0


async def test_reclassify_strict_validation_malformed_json(rt_env: dict[str, Any]) -> None:
    rt_env["provider"] = _make_provider("not json at all")
    rt_env["pages"] = [_fake_page("A")]
    summary = await rt.run_reclassify(vault_id="test-vault")
    assert summary.failed == 1
    assert summary.changed == 0


async def test_reclassify_reserved_type_untouched(rt_env: dict[str, Any]) -> None:
    # An overview page that slips into the candidate list must never be reclassified.
    reserved = _fake_page("Overview", page_type="overview")
    normal = _fake_page("A", page_type="concept")
    rt_env["pages"] = [reserved, normal]
    summary = await rt.run_reclassify(vault_id="test-vault")
    assert summary.skipped >= 1
    assert reserved.page_type == "overview", "reserved type must stay untouched"
    # Only the normal page was classified + changed.
    assert summary.changed == 1
    assert {a["page_id"] for a in rt_env["applied"]} == {normal.id}


async def test_reclassify_max_pages_cap(rt_env: dict[str, Any]) -> None:
    rt_env["pages"] = [_fake_page("A"), _fake_page("B"), _fake_page("C")]
    summary = await rt.run_reclassify(vault_id="test-vault", max_pages=1)
    assert summary.processed == 1
    assert summary.stopped_reason == "maxpages"
    assert rt_env["provider"]._chat_calls[0] == 1


async def test_reclassify_token_budget_cap(rt_env: dict[str, Any]) -> None:
    from app.ingest.schemas import Usage

    provider = rt_env["provider"]
    acc_holder: dict[str, Any] = {}

    def capture_bind(accumulator: Any) -> None:
        acc_holder["acc"] = accumulator

    provider.bind_accumulator = capture_bind

    async def mock_chat(*, messages: list[Any], retrieval_context: str = "") -> AsyncIterator[str]:
        provider._chat_calls[0] += 1
        acc_holder["acc"].add(Usage(input_tokens=100, output_tokens=0, total_cost_usd=0.0))

        async def _gen() -> AsyncIterator[str]:
            yield '{"type": "entity"}'

        return _gen()

    provider.chat = mock_chat

    rt_env["pages"] = [_fake_page("A"), _fake_page("B"), _fake_page("C")]
    summary = await rt.run_reclassify(vault_id="test-vault", token_budget=50)
    assert summary.stopped_reason == "budget"
    assert summary.processed == 1
    assert provider._chat_calls[0] == 1


async def test_reclassify_no_provider_is_error(rt_env: dict[str, Any]) -> None:
    rt_env["provider"] = None
    rt_env["pages"] = [_fake_page("A")]
    summary = await rt.run_reclassify(vault_id="test-vault")
    assert summary.stopped_reason == "error"
    assert summary.processed == 0


async def test_reclassify_max_pages_hard_cap() -> None:
    mp, _tb = rt.clamp_bounds(999_999, None)
    assert mp == rt.MAX_PAGES_HARD_CAP


async def test_reclassify_type_case_insensitive(rt_env: dict[str, Any]) -> None:
    # Uppercase / fenced answer is normalized to the canonical lowercase type.
    rt_env["provider"] = _make_provider('```json\n{"type": "SOURCE"}\n```')
    rt_env["pages"] = [_fake_page("A", page_type="concept")]
    summary = await rt.run_reclassify(vault_id="test-vault")
    assert summary.changed == 1
    assert summary.by_type == {"source": 1}
    assert rt_env["applied"][0]["type"] == "source"


async def test_reclassify_single_flight_state(rt_env: dict[str, Any]) -> None:
    import asyncio

    rt_env["pages"] = [_fake_page("A")]
    seen: dict[str, bool] = {}

    orig_load = rt._load_candidate_pages

    async def slow_load(vault_id: str, max_pages: int, force: bool) -> list[Any]:
        seen["running_during"] = rt.is_running()
        await asyncio.sleep(0)
        return await orig_load(vault_id, max_pages, force)

    rt._load_candidate_pages = slow_load  # type: ignore[assignment]
    try:
        assert not rt.is_running()
        await rt.run_reclassify(vault_id="test-vault")
    finally:
        rt._load_candidate_pages = orig_load  # type: ignore[assignment]
    assert seen["running_during"] is True
    assert not rt.is_running()
    assert rt.get_last_summary() is not None
