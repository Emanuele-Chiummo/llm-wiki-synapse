"""
ADR-0054 §4 (F18 / R12-2) domain backfill tests — infra-free.

Covers:
  * dormant vocabulary → returns immediately, zero provider calls (stopped_reason=dormant).
  * caps: max_pages (stopped_reason=maxpages), token_budget (stopped_reason=budget).
  * idempotency: force=false skips already-domain-tagged pages; force=true re-classifies.
  * single-flight state: is_running / get_last_summary exposed for the endpoint.

DB + write-back primitives are stubbed (no Postgres, no files). The vocabulary + provider
resolution are monkeypatched so the suite passes standalone.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from app.ops import backfill_domains as bf


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


def _fake_page(title: str, tags: list[str] | None = None) -> Any:
    p = type("P", (), {})()
    p.id = uuid.uuid4()
    p.vault_id = "test-vault"
    p.title = title
    p.file_path = f"wiki/concepts/{title.lower().replace(' ', '-')}.md"
    p.page_type = "concept"
    p.sources = ["raw/x.md"]
    p.source_mtime_ns = 0
    p.tags = tags
    return p


VOCAB = ["ServiceNow", "SAM"]


@pytest.fixture()
def bf_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub vocabulary, provider, candidate query, and the orchestrator write-back primitives."""
    import app.config_overrides as cfg_ov
    import app.ingest.orchestrator as orch

    state: dict[str, Any] = {
        "vocabulary": VOCAB,
        "pages": [],
        "applied": [],
        "bumps": 0,
        "provider": _make_provider('{"domains": ["ServiceNow"]}'),
        "body": "page body",
    }

    def fake_vocab() -> list[str]:
        return state["vocabulary"]

    async def fake_resolve(vault_id: str) -> Any:
        prov = state["provider"]
        return None if prov is None else (prov, object())

    async def fake_load(vault_id: str, max_pages: int) -> list[Any]:
        return list(state["pages"])[:max_pages]

    async def fake_apply(page: Any, new_tags: list[str]) -> None:
        page.tags = new_tags or None
        state["applied"].append({"page_id": page.id, "tags": new_tags})

    async def fake_bump() -> None:
        state["bumps"] += 1

    def fake_read_body(page: Any) -> str:
        return state["body"]

    monkeypatch.setattr(cfg_ov, "effective_domain_vocabulary", fake_vocab, raising=False)
    monkeypatch.setattr(bf, "resolve_operation_provider", fake_resolve)
    monkeypatch.setattr(bf, "_load_candidate_pages", fake_load)
    monkeypatch.setattr(orch, "apply_domain_tags", fake_apply)
    monkeypatch.setattr(orch, "bump_version", fake_bump)
    monkeypatch.setattr(orch, "_read_body_for_classification", fake_read_body)

    # Reset the module-level single-flight state between tests.
    bf._state.is_running = False
    bf._state.last_summary = None
    bf._state.current = {}
    return state


async def test_backfill_dormant_vocabulary(bf_env: dict[str, Any]) -> None:
    bf_env["vocabulary"] = []
    summary = await bf.run_backfill(vault_id="test-vault")
    assert summary.stopped_reason == "dormant"
    assert summary.tagged == 0
    assert bf_env["provider"]._chat_calls[0] == 0
    assert not bf.is_running()
    assert bf.get_last_summary() is summary


async def test_backfill_tags_all_untagged_pages(bf_env: dict[str, Any]) -> None:
    bf_env["pages"] = [_fake_page("A"), _fake_page("B"), _fake_page("C")]
    summary = await bf.run_backfill(vault_id="test-vault", max_pages=500)
    assert summary.processed == 3
    assert summary.tagged == 3
    assert summary.skipped == 0
    assert summary.stopped_reason == "complete"
    assert bf_env["provider"]._chat_calls[0] == 3
    assert bf_env["bumps"] == 1, "one data_version bump for the whole batch (§4.3)"


async def test_backfill_max_pages_cap(bf_env: dict[str, Any]) -> None:
    bf_env["pages"] = [_fake_page("A"), _fake_page("B"), _fake_page("C")]
    summary = await bf.run_backfill(vault_id="test-vault", max_pages=1)
    # Candidate query is LIMIT max_pages → exactly one page seen.
    assert summary.processed == 1
    assert summary.tagged == 1
    assert summary.stopped_reason == "maxpages"
    assert bf_env["provider"]._chat_calls[0] == 1


async def test_backfill_token_budget_cap(bf_env: dict[str, Any]) -> None:
    # A provider that accrues tokens so the budget gate trips before the 2nd page.
    from app.ingest.schemas import Usage

    provider = bf_env["provider"]
    acc_holder: dict[str, Any] = {}

    def capture_bind(accumulator: Any) -> None:
        acc_holder["acc"] = accumulator

    provider.bind_accumulator = capture_bind

    async def mock_chat(*, messages: list[Any], retrieval_context: str = "") -> AsyncIterator[str]:
        provider._chat_calls[0] += 1
        acc_holder["acc"].add(Usage(input_tokens=100, output_tokens=0, total_cost_usd=0.0))

        async def _gen() -> AsyncIterator[str]:
            yield '{"domains": ["ServiceNow"]}'

        return _gen()

    provider.chat = mock_chat

    bf_env["pages"] = [_fake_page("A"), _fake_page("B"), _fake_page("C")]
    # Budget of 50 tokens: first page spends 100 → 2nd iteration's top-of-loop gate trips.
    summary = await bf.run_backfill(vault_id="test-vault", token_budget=50)
    assert summary.stopped_reason == "budget"
    assert summary.processed == 1
    assert provider._chat_calls[0] == 1


async def test_backfill_idempotent_skip(bf_env: dict[str, Any]) -> None:
    tagged = _fake_page("A", tags=["domain/ServiceNow"])
    untagged = _fake_page("B")
    bf_env["pages"] = [tagged, untagged]

    summary = await bf.run_backfill(vault_id="test-vault", force=False)
    assert summary.skipped == 1  # the already-tagged page skipped
    assert summary.processed == 1
    assert summary.tagged == 1
    assert bf_env["provider"]._chat_calls[0] == 1, "already-tagged page not re-classified"


async def test_backfill_force_reclassifies(bf_env: dict[str, Any]) -> None:
    tagged = _fake_page("A", tags=["domain/OldDomain", "workflow"])
    bf_env["pages"] = [tagged]

    summary = await bf.run_backfill(vault_id="test-vault", force=True)
    assert summary.skipped == 0
    assert summary.processed == 1
    assert bf_env["provider"]._chat_calls[0] == 1
    # Old domain replaced; user tag preserved.
    assert tagged.tags == ["workflow", "domain/ServiceNow"]


async def test_backfill_max_pages_hard_cap() -> None:
    mp, _tb = bf.clamp_bounds(999_999, None)
    assert mp == bf.MAX_PAGES_HARD_CAP


async def test_backfill_single_flight_state(bf_env: dict[str, Any]) -> None:
    # While a run is in flight, is_running() is True; it clears afterwards.
    import asyncio

    bf_env["pages"] = [_fake_page("A")]
    seen: dict[str, bool] = {}

    orig_load = bf._load_candidate_pages

    async def slow_load(vault_id: str, max_pages: int) -> list[Any]:
        seen["running_during"] = bf.is_running()
        await asyncio.sleep(0)
        return await orig_load(vault_id, max_pages)

    bf._load_candidate_pages = slow_load  # type: ignore[assignment]
    try:
        assert not bf.is_running()
        await bf.run_backfill(vault_id="test-vault")
    finally:
        bf._load_candidate_pages = orig_load  # type: ignore[assignment]
    assert seen["running_during"] is True
    assert not bf.is_running()
    assert bf.get_last_summary() is not None
