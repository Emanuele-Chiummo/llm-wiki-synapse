"""Unit tests for the block-based orchestrated loop (ADR-0076, app.ingest.block_loop).

A scripted InferenceProvider whose ``complete()`` returns a fixed sequence (analysis, then one or
more generations, then an optional review-stage output) drives run_block_loop. No network.

Covers:
  • convergence: ≥2 FILE blocks incl. [[wikilinks]] + an inline REVIEW block are parsed/returned,
  • lang-less frontmatter is accepted (NO ``lang`` gate — unlike the JSON loop's validate_pages),
  • empty generation (0 FILE blocks) fails → retries → non-converged at max_iter,
  • a recovered retry augments the generation user message with the prior errors and converges,
  • the dedicated review stage fires once the FILE-block / char threshold is crossed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.ingest.block_loop import run_block_loop
from app.ingest.provider.base import InferenceProvider, UsageAccumulator
from app.ingest.schemas import (
    Analysis,
    Message,
    ProviderCapabilities,
    Usage,
    WikiPage,
)

ORIGIN = "raw/sources/doc.md"

ANALYSIS = "## Key Entities\n- Acme Corp\n\n## Recommendations\n- Create entity + concept pages."

GEN_TWO_FILES = """---FILE: wiki/entities/acme.md---
---
type: entity
title: Acme Corp
created: 2026-07-14
updated: 2026-07-14
sources: [doc.md]
---

# Acme Corp

Acme builds the [[Widget Platform]].
---END FILE---

---FILE: wiki/concepts/widget-platform.md---
---
type: concept
title: Widget Platform
created: 2026-07-14
updated: 2026-07-14
sources: [doc.md]
---

# Widget Platform

The platform maintained by [[Acme Corp]].
---END FILE---

---REVIEW: missing-page | Competitor Landscape---
A comparison of competing widget platforms would help.
OPTIONS: Create Page | Skip
SEARCH: widget platform competitors | acme rivals market share
---END REVIEW---
"""

REVIEW_STAGE_OUTPUT = """---REVIEW: suggestion | Deep-dive on adoption metrics---
Adoption metrics for the platform are worth a dedicated page.
OPTIONS: Create Page | Skip
SEARCH: widget platform adoption metrics | acme customer growth
---END REVIEW---
"""


class _ScriptedProvider(InferenceProvider):
    """Returns a fixed list of ``complete()`` responses in order (records every call)."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str, int]] = []

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            mode="local",
            supports_tools=False,
            supports_agentic_loop=False,
            max_context=8192,
            name="Scripted",
        )

    async def analyze(self, source_text: str, vault_context: str) -> Analysis:  # pragma: no cover
        raise NotImplementedError

    async def generate(  # pragma: no cover
        self, analysis: Analysis, retrieval_context: str, source_text: str = ""
    ) -> list[WikiPage]:
        raise NotImplementedError

    async def chat(
        self, messages: list[Message], retrieval_context: str
    ) -> AsyncIterator[str]:  # pragma: no cover
        raise NotImplementedError

    async def complete(self, system: str, prompt: str, *, max_tokens: int) -> str:
        self.calls.append((system, prompt, max_tokens))
        self._record_usage(Usage(input_tokens=10, output_tokens=5, total_cost_usd=0.0))
        return self._responses.pop(0)


def _run(provider: _ScriptedProvider, **overrides: object):
    kwargs: dict[str, object] = {
        "provider": provider,
        "accumulator": UsageAccumulator(),
        "source_text": "The Acme Corp report describes its Widget Platform.",
        "purpose": "",
        "schema": "",  # empty routing ⇒ no routing constraint (all safe wiki/ paths valid)
        "index": "",
        "source_filename": "doc.md",
        "origin_source": ORIGIN,
        "language_name": None,
        "max_iter": 3,
        "token_budget": 60_000,
        "review_stage_min_chars": 10_000,
        "review_stage_min_file_blocks": 4,
    }
    kwargs.update(overrides)
    return run_block_loop(**kwargs)  # type: ignore[arg-type]


async def test_converges_with_files_and_inline_review() -> None:
    provider = _ScriptedProvider([ANALYSIS, GEN_TWO_FILES])
    result = await _run(provider)

    assert result.converged is True
    assert result.stop_reason == "converged"
    assert result.iterations == 1
    assert result.analysis_text == ANALYSIS
    # Two FILE blocks parsed + sanitized, each carrying its [[wikilink]].
    assert [b.path for b in result.file_blocks] == [
        "wiki/entities/acme.md",
        "wiki/concepts/widget-platform.md",
    ]
    assert "[[Widget Platform]]" in result.file_blocks[0].content
    # The inline REVIEW block is collected even though the dedicated stage did not fire.
    assert any(rb.title == "Competitor Landscape" for rb in result.review_blocks)
    # Exactly two provider calls: analysis + one generation (no dedicated review stage).
    assert len(provider.calls) == 2


async def test_langless_frontmatter_is_accepted() -> None:
    # None of the FILE blocks carry a `lang:` key; the block validator must NOT require one.
    provider = _ScriptedProvider([ANALYSIS, GEN_TWO_FILES])
    result = await _run(provider)

    assert result.converged is True
    for block in result.file_blocks:
        assert "lang:" not in block.content.split("---", 2)[1]  # no lang in the frontmatter block


async def test_empty_generation_retries_then_nonconverged() -> None:
    provider = _ScriptedProvider(
        [ANALYSIS, "no file blocks here", "still nothing useful", "nope, prose only"]
    )
    result = await _run(provider, max_iter=3)

    assert result.converged is False
    assert result.stop_reason == "max_iter"
    assert result.iterations == 3
    assert result.file_blocks == []  # last batch kept (empty)
    assert result.review_blocks == []
    # analysis + three generation attempts.
    assert len(provider.calls) == 4


async def test_retry_augments_user_message_then_converges() -> None:
    provider = _ScriptedProvider([ANALYSIS, "prose, no blocks", GEN_TWO_FILES])
    result = await _run(provider, max_iter=3)

    assert result.converged is True
    assert result.iterations == 2
    # The SECOND generation call (index 2 overall) carries the augmented validation-error block.
    second_generation_user = provider.calls[2][1]
    assert "Validation errors from the previous attempt" in second_generation_user
    assert "no FILE blocks" in second_generation_user


async def test_review_stage_fires_past_threshold() -> None:
    provider = _ScriptedProvider([ANALYSIS, GEN_TWO_FILES, REVIEW_STAGE_OUTPUT])
    # Lower the FILE-block threshold so the two-block generation triggers the dedicated stage.
    result = await _run(provider, review_stage_min_file_blocks=2)

    # analysis + generation + the dedicated review-stage call.
    assert len(provider.calls) == 3
    titles = {rb.title for rb in result.review_blocks}
    # Both the inline REVIEW block and the dedicated-stage REVIEW block are returned (deduped).
    assert "Competitor Landscape" in titles
    assert "Deep-dive on adoption metrics" in titles


async def test_token_budget_stops_before_generation() -> None:
    # A budget already exhausted by the analysis call stops the loop before any generation.
    provider = _ScriptedProvider([ANALYSIS])
    result = await _run(provider, token_budget=1)

    assert result.converged is False
    assert result.stop_reason == "token_budget"
    assert result.iterations == 0
    assert len(provider.calls) == 1  # only the analysis call was made
