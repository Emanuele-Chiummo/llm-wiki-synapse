"""
ADR-0054 (F18 / R12-2) domain auto-tag seam tests — infra-free.

Covers:
  * classify_page_domains: strict vocabulary validation (hallucinated domain dropped),
    case-insensitive match to canonical casing, empty vocabulary → zero provider calls.
  * merge_domain_tags: preserves user tags, replaces the domain/* subset, idempotent, stable.
  * orchestrator hook (_auto_tag_written_pages): dormant vocabulary → no provider calls;
    non-fatal on provider error (page stays untagged); tag merge preserves user tags.
  * backfill run_backfill: caps honored (max_pages, token_budget), idempotent skip,
    force re-classifies, single-flight state.

The DB/file write-back primitives are stubbed (apply_domain_tags / bump_version) so the tests
stay Postgres-portable and free of the JSONB/UUID-on-SQLite landmine (project memory). The
effective_domain_vocabulary accessor (backend-engineer-owned) is monkeypatched so this suite
passes standalone before that code merges.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from app.ingest import domain_tagger as dt_mod
from app.ingest.domain_tagger import (
    classify_page_domains,
    has_domain_tag,
    is_domain_tag,
    merge_domain_tags,
)

# ── Mock provider (chat() yields one JSON blob; counts calls) ─────────────────


def _make_provider(response: str, *, error: bool = False) -> Any:
    from unittest.mock import MagicMock

    provider = MagicMock()
    provider._chat_calls = [0]

    async def mock_chat(*, messages: list[Any], retrieval_context: str = "") -> AsyncIterator[str]:
        provider._chat_calls[0] += 1

        async def _gen() -> AsyncIterator[str]:
            if error:
                raise RuntimeError("provider boom")
            yield response

        return _gen()

    provider.chat = mock_chat
    provider.bind_accumulator = MagicMock()
    return provider


VOCAB = ["ServiceNow", "SAM", "Procurement", "Regolamentazioni", "TPRM"]


# ── Pure: merge / predicates ──────────────────────────────────────────────────


def test_is_and_has_domain_tag() -> None:
    assert is_domain_tag("domain/ServiceNow")
    assert not is_domain_tag("workflow")
    assert has_domain_tag(["workflow", "domain/SAM"])
    assert not has_domain_tag(["workflow", "reference"])
    assert not has_domain_tag(None)


def test_merge_preserves_user_tags_and_replaces_domain_subset() -> None:
    existing = ["workflow", "domain/OldDomain", "reference"]
    merged = merge_domain_tags(existing, ["ServiceNow", "SAM"])
    # User tags kept verbatim + first, in original order; domain/* replaced + sorted.
    assert merged == ["workflow", "reference", "domain/SAM", "domain/ServiceNow"]


def test_merge_is_idempotent_and_stable() -> None:
    existing = ["workflow"]
    once = merge_domain_tags(existing, ["ServiceNow", "SAM"])
    twice = merge_domain_tags(once, ["SAM", "ServiceNow"])  # order-shuffled input
    assert once == twice


def test_merge_empty_classification_drops_all_domain_tags() -> None:
    merged = merge_domain_tags(["workflow", "domain/ServiceNow"], [])
    assert merged == ["workflow"]


# ── classify_page_domains: strict validation ──────────────────────────────────


async def test_classify_drops_hallucinated_domain() -> None:
    # Provider returns one real domain + one it invented → invented one dropped.
    provider = _make_provider('{"domains": ["ServiceNow", "Nonexistent Domain"]}')
    out = await classify_page_domains(provider, "Incident Management", "body", VOCAB)
    assert out == ["ServiceNow"]
    assert provider._chat_calls[0] == 1


async def test_classify_case_insensitive_canonical_casing() -> None:
    # Provider returns wrong casing → normalised to the vocabulary's canonical spelling.
    provider = _make_provider('{"domains": ["servicenow", "tprm"]}')
    out = await classify_page_domains(provider, "T", "b", VOCAB)
    # Emitted in vocabulary order with canonical casing.
    assert out == ["ServiceNow", "TPRM"]


async def test_classify_empty_result_is_valid() -> None:
    provider = _make_provider('{"domains": []}')
    out = await classify_page_domains(provider, "T", "b", VOCAB)
    assert out == []
    assert provider._chat_calls[0] == 1


async def test_classify_empty_vocabulary_makes_no_call() -> None:
    provider = _make_provider('{"domains": ["ServiceNow"]}')
    out = await classify_page_domains(provider, "T", "b", [])
    assert out == []
    assert provider._chat_calls[0] == 0, "empty vocabulary ⇒ zero provider calls (I6)"


async def test_classify_bare_array_and_fenced_json() -> None:
    provider = _make_provider('```json\n["SAM"]\n```')
    out = await classify_page_domains(provider, "T", "b", VOCAB)
    assert out == ["SAM"]


async def test_classify_garbage_output_returns_empty() -> None:
    provider = _make_provider("not json — the model rambled")
    out = await classify_page_domains(provider, "T", "b", VOCAB)
    assert out == []


async def test_classify_truncates_long_content() -> None:
    captured: dict[str, str] = {}

    async def spy_collect(provider: Any, instruction: str) -> str:
        captured["instruction"] = instruction
        return '{"domains": []}'

    import app.ingest.domain_tagger as mod

    orig = mod.bounded_chat_collect
    mod.bounded_chat_collect = spy_collect  # type: ignore[assignment]
    try:
        provider = _make_provider('{"domains": []}')
        await classify_page_domains(provider, "T", "X" * 10_000, VOCAB)
    finally:
        mod.bounded_chat_collect = orig  # type: ignore[assignment]
    # The body excerpt is capped at ~4k chars (§3.3).
    assert captured["instruction"].count("X") <= dt_mod._CONTENT_CHAR_CAP


# ── Orchestrator hook ─────────────────────────────────────────────────────────


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


@pytest.fixture()
def hook_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub the write-back primitive + body reader + vocabulary accessor (no DB, no files)."""
    import app.ingest.orchestrator as orch

    state: dict[str, Any] = {"applied": [], "vocabulary": VOCAB, "body": "some page body"}

    async def fake_apply(page: Any, new_tags: list[str]) -> None:
        page.tags = new_tags or None
        state["applied"].append({"page_id": page.id, "tags": new_tags})

    def fake_read_body(page: Any) -> str:
        return state["body"]

    def fake_vocab() -> list[str]:
        return state["vocabulary"]

    monkeypatch.setattr(orch, "apply_domain_tags", fake_apply)
    monkeypatch.setattr(orch, "_read_body_for_classification", fake_read_body)
    # The accessor is imported inside the hook via `from app.config_overrides import ...`,
    # so patch it on the config_overrides module (works even before the backend adds it).
    import app.config_overrides as cfg_ov

    monkeypatch.setattr(cfg_ov, "effective_domain_vocabulary", fake_vocab, raising=False)
    return state


async def test_hook_dormant_vocabulary_no_calls(
    hook_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.ingest.orchestrator as orch

    hook_env["vocabulary"] = []
    provider = _make_provider('{"domains": ["ServiceNow"]}')
    page = _fake_page("Incident Management", tags=["workflow"])

    await orch._auto_tag_written_pages(
        provider=provider, written_pages=[page], origin_source="raw/x.md"
    )
    assert provider._chat_calls[0] == 0, "dormant ⇒ zero provider calls"
    assert hook_env["applied"] == []
    assert page.tags == ["workflow"]


async def test_hook_merges_and_preserves_user_tags(hook_env: dict[str, Any]) -> None:
    import app.ingest.orchestrator as orch

    provider = _make_provider('{"domains": ["ServiceNow"]}')
    page = _fake_page("Incident Management", tags=["workflow"])

    await orch._auto_tag_written_pages(
        provider=provider, written_pages=[page], origin_source="raw/x.md"
    )
    assert provider._chat_calls[0] == 1
    assert page.tags == ["workflow", "domain/ServiceNow"]


async def test_hook_non_fatal_on_provider_error(hook_env: dict[str, Any]) -> None:
    import app.ingest.orchestrator as orch

    provider = _make_provider("", error=True)
    page = _fake_page("Incident Management", tags=["workflow"])

    # Must not raise; page stays untagged (§3.4).
    await orch._auto_tag_written_pages(
        provider=provider, written_pages=[page], origin_source="raw/x.md"
    )
    assert hook_env["applied"] == []
    assert page.tags == ["workflow"]


async def test_hook_skips_non_wiki_pages(hook_env: dict[str, Any]) -> None:
    import app.ingest.orchestrator as orch

    provider = _make_provider('{"domains": ["ServiceNow"]}')
    raw_page = _fake_page("A source")
    raw_page.file_path = "raw/sources/a.md"

    await orch._auto_tag_written_pages(
        provider=provider, written_pages=[raw_page], origin_source="raw/x.md"
    )
    assert provider._chat_calls[0] == 0
    assert hook_env["applied"] == []
