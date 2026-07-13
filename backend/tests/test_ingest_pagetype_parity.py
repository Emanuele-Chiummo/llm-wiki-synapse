"""
ADR-0063 §2.4 — nashsu/llm_wiki page-type parity.

Covers the three parity guarantees that bring Synapse's ingest page-type distribution in line
with llm_wiki while keeping every derived page grounded in the source:

  (a) GENERATE_SYSTEM / ANALYZE_SYSTEM expose all six user-content page types and allow direct
      query/comparison/synthesis generation only when the source supports the derived page.
  (b) _ensure_source_summary ALWAYS yields a source page traceable to the origin — even when the
      model produced entity/concept pages but omitted the source summary (ingest.ts:1209-1244).
  (c) no duplicate source page is synthesized when one already cites the origin (dedupe / churn).

All provider interaction is mocked — no real model is called.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.ingest.loop import validate_pages
from app.ingest.orchestrator import _ensure_source_summary
from app.ingest.provider._common import (
    ANALYZE_SYSTEM,
    GENERATE_SYSTEM,
    GENERATION_SCAFFOLD,
    build_generate_prompt,
)
from app.ingest.schemas import Analysis, PageType, WikiFrontmatter, WikiPage

ORIGIN = "raw/sources/example.md"


def _analysis(language: str = "en", summary: str | None = "A short summary.") -> Analysis:
    return Analysis(
        topics=["topic"],
        entities=["Thing"],
        language=language,
        suggested_pages=[{"title": "Thing", "type": PageType.ENTITY}],
        summary=summary,
    )


def _page(page_type: PageType, title: str, sources: list[str]) -> WikiPage:
    fm = WikiFrontmatter(type=page_type, title=title, sources=sources, lang="en")
    return WikiPage(title=title, type=page_type, content=f"# {title}\n\nbody", frontmatter=fm)


# ── (a) source-grounded six-type generation contract ──────────────────────────────


def test_generate_system_has_source_grounded_six_type_scaffold() -> None:
    lowered = GENERATE_SYSTEM.lower()
    # Exactly-one source page + all six user-content types are available.
    assert "what to generate" in lowered
    assert "exactly one source-summary page" in lowered
    assert "type=entity" in lowered
    assert "type=concept" in lowered
    assert "entity|concept|source|query|synthesis|comparison" in GENERATE_SYSTEM


def test_generate_system_allows_only_source_supported_derived_pages() -> None:
    lowered = GENERATE_SYSTEM.lower()
    assert "type=query" in lowered
    assert "type=comparison" in lowered
    assert "type=synthesis" in lowered
    assert "directly supported by this source" in lowered
    assert "do not create synthesis or comparison pages during ingest" not in lowered
    assert "## research queries" in lowered
    assert "title-only/generic stubs are invalid" in lowered


def test_query_page_requires_contextual_retrieval_queries() -> None:
    weak = _page(PageType.QUERY, "How does procurement change?", [ORIGIN])
    assert any("Research queries" in error for error in validate_pages([weak], ORIGIN))

    strong = _page(PageType.QUERY, "How does procurement change?", [ORIGIN])
    strong.content = (
        "# How does procurement change?\n\n"
        "The source connects procurement operating models with category governance, adoption, "
        "and outcomes.\n\n"
        "## Research queries\n"
        "- procurement operating model transformation evidence\n"
        "- category governance adoption measurable outcomes\n"
    )
    assert validate_pages([strong], ORIGIN) == []


def test_query_page_rejects_generic_placeholder_lists() -> None:
    generic = _page(PageType.QUERY, "How does procurement change?", [ORIGIN])
    generic.content = (
        "# How does procurement change?\n\n"
        "## Research queries\n"
        "- tell me something else\n"
        "- what should I know\n"
    )

    assert any("contextual retrieval" in error for error in validate_pages([generic], ORIGIN))

    generic_it = _page(PageType.QUERY, "Come cambia l'approvvigionamento?", [ORIGIN])
    generic_it.frontmatter.lang = "it"
    generic_it.content = (
        "# Come cambia l'approvvigionamento?\n\n"
        "## Research queries\n"
        "- dimmi qualcosa di più\n"
        "- cosa dovrei sapere ancora\n"
    )

    assert any("contextual retrieval" in error for error in validate_pages([generic_it], ORIGIN))


def test_query_page_rejects_generic_synonyms_without_context_anchors() -> None:
    generic_en = _page(PageType.QUERY, "How does procurement change?", [ORIGIN])
    generic_en.content = (
        "# How does procurement change?\n\n"
        "## Research queries\n"
        "- summarize available relevant material\n"
        "- identify useful related resources\n"
    )
    assert any("contextual retrieval" in error for error in validate_pages([generic_en], ORIGIN))

    generic_it = _page(PageType.QUERY, "Come cambia l'approvvigionamento?", [ORIGIN])
    generic_it.frontmatter.lang = "it"
    generic_it.content = (
        "# Come cambia l'approvvigionamento?\n\n"
        "## Research queries\n"
        "- raccontami altre cose utili\n"
        "- riassumi materiale rilevante disponibile\n"
    )
    assert any("contextual retrieval" in error for error in validate_pages([generic_it], ORIGIN))


def test_analyze_system_conservatism_clause() -> None:
    lowered = ANALYZE_SYSTEM.lower()
    assert "entity|concept|source|query|synthesis|comparison" in ANALYZE_SYSTEM
    assert "only when the source actually supports" in lowered
    assert "unresolved question" in lowered
    assert "explicitly compares" in lowered
    assert "integrates multiple claims" in lowered


def test_analyze_system_has_subject_boundary_rule() -> None:
    # nashsu/llm_wiki ingest.ts:1949 — claims must stay attached to their named subject.
    lowered = ANALYZE_SYSTEM.lower()
    assert "do not transfer claims" in lowered
    assert "just because they share keywords" in lowered


def test_generation_scaffold_has_subject_boundary_rule() -> None:
    # nashsu/llm_wiki ingest.ts:2070-2072 — the three subject-boundary bullets, provider-neutral.
    lowered = GENERATION_SCAFFOLD.lower()
    assert "subject boundaries" in lowered
    assert "do not merge or generalize a claim about one subject" in lowered
    assert "write it explicitly as a comparison" in lowered


def test_build_generate_prompt_restates_scaffold() -> None:
    prompt = build_generate_prompt(_analysis(), retrieval_context="")
    assert GENERATION_SCAFFOLD in prompt
    assert "Return the pages JSON now." in prompt


def test_generation_scaffold_is_provider_neutral() -> None:
    # No hardcoded backend / model id / endpoint leaked into the shared scaffold (I6).
    for banned in ("ollama", "anthropic", "claude-", "openai", "http://", "https://", "base_url"):
        assert banned not in GENERATION_SCAFFOLD.lower()


# ── (b) mandatory source page ─────────────────────────────────────────────────────


def test_ensure_source_summary_empty_batch_yields_source_page() -> None:
    out = _ensure_source_summary([], _analysis(), ORIGIN)
    assert len(out) == 1
    assert out[0].type is PageType.SOURCE
    assert ORIGIN in out[0].frontmatter.sources


def test_ensure_source_summary_appends_when_only_entity_concept() -> None:
    # Model produced entity + concept pages but NO source page — the parity fix appends one.
    pages = [
        _page(PageType.ENTITY, "Thing", [ORIGIN]),
        _page(PageType.CONCEPT, "Method", [ORIGIN]),
    ]
    out = _ensure_source_summary(pages, _analysis(), ORIGIN)
    assert len(out) == 3
    # Existing pages preserved and kept first (pages[0] readers unaffected).
    assert out[0].type is PageType.ENTITY
    assert out[1].type is PageType.CONCEPT
    source_pages = [p for p in out if p.type is PageType.SOURCE]
    assert len(source_pages) == 1
    assert ORIGIN in source_pages[0].frontmatter.sources


def test_ensure_source_summary_uses_analysis_summary_for_body() -> None:
    out = _ensure_source_summary([], _analysis(summary="Distinctive summary text."), ORIGIN)
    assert "Distinctive summary text." in out[0].content


def test_ensure_source_summary_language_from_analysis() -> None:
    out = _ensure_source_summary([], _analysis(language="it"), ORIGIN)
    assert out[0].frontmatter.lang == "it"


# ── (c) dedupe / no churn ─────────────────────────────────────────────────────────


def test_ensure_source_summary_no_duplicate_when_source_exists() -> None:
    existing = _page(PageType.SOURCE, "Source: Example", [ORIGIN])
    pages = [_page(PageType.ENTITY, "Thing", [ORIGIN]), existing]
    out = _ensure_source_summary(pages, _analysis(), ORIGIN)
    # Batch returned unchanged (same object identity, no appended stub).
    assert out is pages
    assert len([p for p in out if p.type is PageType.SOURCE]) == 1


def test_ensure_source_summary_source_page_for_other_origin_still_appends() -> None:
    # A source page exists but cites a DIFFERENT origin — this run's origin still needs one.
    other = _page(PageType.SOURCE, "Source: Other", ["raw/sources/other.md"])
    out = _ensure_source_summary([other], _analysis(), ORIGIN)
    origin_source_pages = [
        p for p in out if p.type is PageType.SOURCE and ORIGIN in p.frontmatter.sources
    ]
    assert len(origin_source_pages) == 1
    # The unrelated source page is preserved.
    assert other in out


# ── CLI/delegated route: scaffold reaches the agent's system_prompt (ADR-0063 §7) ──


@pytest.mark.asyncio
async def test_delegated_route_appends_scaffold_to_system_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The delegated (CLI) route appends GENERATION_SCAFFOLD to the agent's system_prompt so the same
    source-grounded six-type contract and "exactly one source page" guarantee reach the CLI backend.
    The deterministic source-page guarantee also runs on the delegated route
    (_ensure_source_summary_for_delegated, nashsu/llm_wiki hasSourceSummary parity — here stubbed).
    """
    from app.ingest import orchestrator as orch

    async def _fake_ingest_context() -> str:
        return "# schema.md\n(rules)"

    monkeypatch.setattr(orch, "_load_ingest_context", _fake_ingest_context)

    captured: dict[str, Any] = {}

    async def _fake_delegate(**kwargs: Any) -> tuple[bool, int, list[str]]:
        captured["system_prompt"] = kwargs.get("system_prompt")
        return True, 1, []

    monkeypatch.setattr(orch, "_delegate_ingest", _fake_delegate)

    # Agentic provider (delegated route).
    provider = MagicMock()
    caps = MagicMock()
    caps.supports_agentic_loop = True
    caps.name = "StubCliProvider"
    caps.mode = "cli"
    provider.capabilities = MagicMock(return_value=caps)
    provider.bind_accumulator = MagicMock()
    monkeypatch.setattr(orch, "resolve_provider", lambda _cfg: provider)
    # F3 language parity: a configured vault language must reach the delegated system_prompt.
    monkeypatch.setattr(orch.settings, "overview_language", "it")

    # Persistence / finalize + fire-and-forget post-hooks stubbed so only routing runs.
    monkeypatch.setattr(orch, "_open_ingest_run", AsyncMock(return_value=uuid.uuid4()))
    monkeypatch.setattr(orch, "_finalize_ingest_run", AsyncMock())
    monkeypatch.setattr(orch, "_update_overview", AsyncMock())
    monkeypatch.setattr(orch, "_ensure_source_summary_for_delegated", AsyncMock(return_value=None))
    monkeypatch.setattr(orch, "_propose_reviews_for_delegated", AsyncMock())
    monkeypatch.setattr(orch, "_purpose_suggestion_for_delegated", AsyncMock())
    monkeypatch.setattr(orch, "_schema_suggestion_for_delegated", AsyncMock())

    handle = MagicMock()
    handle.cancel_event = MagicMock()
    handle.cancel_event.is_set = MagicMock(return_value=False)
    monkeypatch.setattr(orch.ingest_queue, "open_run", MagicMock(return_value=handle))
    monkeypatch.setattr(orch.ingest_queue, "set_route", MagicMock())
    monkeypatch.setattr(orch.ingest_queue, "set_phase", MagicMock())
    monkeypatch.setattr(orch.ingest_queue, "get_retry_count", MagicMock(return_value=0))

    cfg_row = MagicMock()
    cfg_row.model_id = "test-model"
    cfg_row.max_iter = 1
    cfg_row.token_budget = 1000

    try:
        await orch.run_ingest_pipeline(
            provider_config_row=cfg_row,
            source_text="hello world",
            origin_source=ORIGIN,
            abs_source="/tmp/doc.md",
        )
    except Exception:  # noqa: BLE001 — downstream hooks may fail on stubs; we captured already.
        pass

    assert captured.get("system_prompt") is not None
    assert GENERATION_SCAFFOLD in captured["system_prompt"]
    assert "type=query" in captured["system_prompt"].lower()
    assert "type=comparison" in captured["system_prompt"].lower()
    assert "type=synthesis" in captured["system_prompt"].lower()
    # The base ingest context is still present (scaffold is appended, not replacing it).
    assert "schema.md" in captured["system_prompt"]
    # F3 language parity: the mandatory-output-language directive (vault language) is present.
    assert "Mandatory output language" in captured["system_prompt"]
    assert "it" in captured["system_prompt"]


# ── CLI/delegated route: deterministic source-summary guarantee (llm_wiki parity) ──


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _FakeResult:
        return self

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    async def execute(self, *_a: Any, **_k: Any) -> _FakeResult:
        return _FakeResult(self._rows)

    def expunge(self, _r: Any) -> None:  # no-op (rows are plain fakes)
        pass


def _fake_get_session(rows: list[Any]) -> Any:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _cm() -> Any:
        yield _FakeSession(rows)

    return _cm


def _row(page_type: str, sources: list[str]) -> MagicMock:
    r = MagicMock()
    r.page_type = page_type
    r.sources = sources
    r.title = f"row-{page_type}"
    return r


@pytest.mark.asyncio
async def test_delegated_source_summary_skips_when_agent_wrote_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agent already wrote a source page for the origin → no fallback, no duplicate (dedupe guard)."""
    from app.ingest import orchestrator as orch

    existing = _row("source", [ORIGIN])
    monkeypatch.setattr(orch, "get_session", _fake_get_session([existing]))
    write_spy = AsyncMock()
    monkeypatch.setattr(orch, "write_wiki_page", write_spy)

    out = await orch._ensure_source_summary_for_delegated(
        vault_id="v", written_page_ids=["id-1"], origin_source=ORIGIN
    )
    assert out is None
    write_spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_delegated_source_summary_writes_fallback_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agent wrote only entity/concept pages → the fallback source page is synthesized + written."""
    from app.ingest import orchestrator as orch

    only_entity = _row("entity", [ORIGIN])
    monkeypatch.setattr(orch, "get_session", _fake_get_session([only_entity]))

    written = MagicMock()
    written.id = uuid.uuid4()
    written.title = "Source: example"
    write_spy = AsyncMock(return_value=written)
    monkeypatch.setattr(orch, "write_wiki_page", write_spy)

    out = await orch._ensure_source_summary_for_delegated(
        vault_id="v", written_page_ids=["id-1"], origin_source=ORIGIN
    )
    assert out is written
    write_spy.assert_awaited_once()
    # The synthesized page passed to write_wiki_page is a SOURCE page traceable to the origin.
    written_page_arg = write_spy.await_args.args[1]
    assert written_page_arg.type is PageType.SOURCE
    assert ORIGIN in written_page_arg.frontmatter.sources


@pytest.mark.asyncio
async def test_delegated_source_summary_no_ids_writes_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No recorded write ids (agent wrote nothing tracked) → still guarantee a source page."""
    from app.ingest import orchestrator as orch

    # get_session must not even be needed when there are no ids; guard by making it raise if used.
    def _boom() -> Any:
        raise AssertionError("get_session should not be called when written_page_ids is empty")

    monkeypatch.setattr(orch, "get_session", _boom)
    written = MagicMock()
    written.id = uuid.uuid4()
    write_spy = AsyncMock(return_value=written)
    monkeypatch.setattr(orch, "write_wiki_page", write_spy)

    out = await orch._ensure_source_summary_for_delegated(
        vault_id="v", written_page_ids=[], origin_source=ORIGIN
    )
    assert out is written
    write_spy.assert_awaited_once()
