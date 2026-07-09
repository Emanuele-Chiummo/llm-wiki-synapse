"""
ADR-0063 §2.4 — nashsu/llm_wiki page-type parity.

Covers the three parity guarantees that bring Synapse's ingest page-type distribution 1:1 with
llm_wiki 0.6.0 (which over-produced synthesis/comparison and dropped source pages before this):

  (a) GENERATE_SYSTEM / ANALYZE_SYSTEM carry the restricted "what to generate" scaffold + the
      synthesis/comparison "review-only" prohibition (ingest.ts:2017-2024 / 2229 / 1961), and
      build_generate_prompt restates the scaffold at the point of generation.
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


# ── (a) prompt scaffold + prohibition ─────────────────────────────────────────────


def test_generate_system_has_restricted_scaffold() -> None:
    lowered = GENERATE_SYSTEM.lower()
    # Exactly-one source page + entity/concept scaffold present.
    assert "what to generate" in lowered
    assert "exactly one source-summary page" in lowered
    assert "type=entity" in lowered
    assert "type=concept" in lowered
    # The narrowed JSON type union no longer offers synthesis/comparison as co-equal outputs.
    assert "entity|concept|source" in GENERATE_SYSTEM
    assert "entity|concept|source|synthesis|comparison" not in GENERATE_SYSTEM


def test_generate_system_prohibits_synthesis_and_comparison() -> None:
    lowered = GENERATE_SYSTEM.lower()
    assert "do not create synthesis or comparison pages during ingest" in lowered
    assert "review queue" in lowered


def test_analyze_system_conservatism_clause() -> None:
    lowered = ANALYZE_SYSTEM.lower()
    # suggested_pages restricted to entity/concept/source, never invent synthesis/comparison/etc.
    assert "restrict suggested_pages to entity, concept, or source" in lowered
    assert "never invent synthesis, comparison" in lowered
    assert "only when the source actually supports" in lowered
    # ANALYZE keeps the full type union in its JSON contract (schema-valid), instruction forbids use.
    assert "entity|concept|source|synthesis|comparison" in ANALYZE_SYSTEM


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
    The delegated (CLI) route appends GENERATION_SCAFFOLD to the agent's system_prompt so the
    synthesis/comparison prohibition + "exactly one source page" restriction reach the CLI backend
    too (partial coverage — the deterministic source-page guarantee stays orchestrated-only, §7).
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

    # Persistence / finalize + fire-and-forget post-hooks stubbed so only routing runs.
    monkeypatch.setattr(orch, "_open_ingest_run", AsyncMock(return_value=uuid.uuid4()))
    monkeypatch.setattr(orch, "_finalize_ingest_run", AsyncMock())
    monkeypatch.setattr(orch, "_update_overview", AsyncMock())
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
    # The base ingest context is still present (scaffold is appended, not replacing it).
    assert "schema.md" in captured["system_prompt"]
