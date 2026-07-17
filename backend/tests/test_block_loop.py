"""Unit tests for the block-based orchestrated loop (ADR-0076, app.ingest.block_loop).

A scripted InferenceProvider whose ``complete()`` returns a fixed sequence (analysis, then one or
more generations, then an optional review-stage output) drives run_block_loop. No network.

Covers:
  • convergence: ≥2 FILE blocks incl. [[wikilinks]] + an inline REVIEW block are parsed/returned,
  • lang-less frontmatter is accepted (NO ``lang`` gate — unlike the JSON loop's validate_pages),
  • empty generation (0 FILE blocks) fails → retries → non-converged at max_iter,
  • a recovered retry augments the generation user message with the prior errors and converges,
  • the dedicated review stage fires once the FILE-block / char threshold is crossed,
  • 1.9.4 W1 (PF-LONGSRC-1): Stage 1 chunked analysis for long sources — over-threshold chunking
    + merge, under-threshold no-op (non-regression), the max_chunks hard cap (I7), and the
    token_budget pre-call guard mid-chunking (I7).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
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
    # 1.9.1 W5 (NC-1): no iteration ever ran, so last_errors stays empty (never a NameError /
    # stale-batch leak) but tokens_used/token_budget are still populated for the UI.
    diag = result.diagnostics()
    assert diag == {
        "stop_reason": "token_budget",
        "iterations": 0,
        "last_errors": [],
        "tokens_used": result.tokens_used,
        "token_budget": 1,
    }


async def test_diagnostics_on_convergence_has_empty_errors() -> None:
    # 1.9.1 W5 (NC-1): a converged run reports stop_reason="converged" with no last_errors.
    provider = _ScriptedProvider([ANALYSIS, GEN_TWO_FILES])
    result = await _run(provider)

    diag = result.diagnostics()
    assert diag["stop_reason"] == "converged"
    assert diag["iterations"] == 1
    assert diag["last_errors"] == []
    assert diag["token_budget"] == 60_000
    assert diag["tokens_used"] == result.tokens_used


async def test_diagnostics_on_max_iter_captures_last_errors() -> None:
    # 1.9.1 W5 (NC-1): the finding NC-1 scenario — max_iter exhausted, validation kept failing.
    # diagnostics() must surface the LAST iteration's errors (not an empty/generic label) so the
    # UI can show "why" instead of a bare "Non convergito".
    provider = _ScriptedProvider(
        [ANALYSIS, "no file blocks here", "still nothing useful", "nope, prose only"]
    )
    result = await _run(provider, max_iter=3)

    diag = result.diagnostics()
    assert diag["stop_reason"] == "max_iter"
    assert diag["iterations"] == 3
    assert diag["last_errors"] != []
    assert any("FILE blocks" in e for e in diag["last_errors"])


# ── 1.9.4 W1 (PF-LONGSRC-1): chunked Stage 1 analysis for long sources ────────────


def _long_text(paragraphs: int, para_chars: int = 6_000) -> str:
    # Distinct paragraphs separated by blank lines so the splitter has boundaries — mirrors
    # test_long_source_chunked.py's helper so both loops are exercised the same way.
    return "\n\n".join(f"Para {i} " + ("x" * para_chars) for i in range(paragraphs))


def _set_long_source_knobs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    threshold: int,
    chunk_chars: int = 6_000,
    max_chunks: int = 8,
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "ingest_long_source_char_threshold", threshold)
    monkeypatch.setattr(settings, "ingest_long_source_chunk_chars", chunk_chars)
    monkeypatch.setattr(settings, "ingest_long_source_max_chunks", max_chunks)


async def test_short_source_below_threshold_is_not_chunked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Chunking is ENABLED (nonzero threshold) but the default short source_text stays under it —
    # must take the exact pre-1.9.4 single whole-source complete() call, no different call shape.
    _set_long_source_knobs(monkeypatch, threshold=5_000, chunk_chars=6_000)
    provider = _ScriptedProvider([ANALYSIS, GEN_TWO_FILES])
    result = await _run(provider)

    assert result.converged is True
    assert len(provider.calls) == 2  # analysis + one generation — identical to the short path
    assert result.analysis_text == ANALYSIS
    analysis_user = provider.calls[0][1]
    assert ORIGIN in analysis_user
    assert "section" not in analysis_user.lower()  # no chunk framing was added


async def test_long_source_triggers_chunked_analysis_and_merges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ingest.long_source import chunk_overlap_chars, split_into_chunks

    _set_long_source_knobs(monkeypatch, threshold=5_000, chunk_chars=6_000)
    long_source = _long_text(4, para_chars=6_000)
    n_chunks = len(split_into_chunks(long_source, 6_000, chunk_overlap_chars(6_000)))
    assert n_chunks >= 2  # sanity: this source really does split into multiple chunks

    chunk_analyses = [f"chunk analysis {i}" for i in range(n_chunks)]
    provider = _ScriptedProvider([*chunk_analyses, GEN_TWO_FILES])
    result = await _run(provider, source_text=long_source)

    assert result.converged is True
    # one complete() call per analyzed chunk + one generation call — chunking never leaks into
    # the generation loop's own call count.
    assert len(provider.calls) == n_chunks + 1
    for text in chunk_analyses:
        assert text in result.analysis_text
    assert result.analysis_text.startswith("## Source section 1/")


async def test_max_chunks_hard_cap_bounds_analysis_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ingest.long_source import chunk_overlap_chars, split_into_chunks

    _set_long_source_knobs(monkeypatch, threshold=5_000, chunk_chars=4_000, max_chunks=2)
    long_source = _long_text(10, para_chars=5_000)
    raw = split_into_chunks(long_source, 4_000, chunk_overlap_chars(4_000))
    assert len(raw) > 2  # the natural split exceeds the cap so the cap actually engages (I7)

    provider = _ScriptedProvider(["c1", "c2", GEN_TWO_FILES])
    result = await _run(provider, source_text=long_source, max_iter=1)

    # exactly 2 analysis calls (HARD cap, I7) + 1 generation call — never one call per chunk of a
    # document that would otherwise split into more than max_chunks pieces.
    assert len(provider.calls) == 3
    assert result.converged is True


async def test_token_budget_stops_further_chunk_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # I7: the token_budget pre-call guard also bounds the chunked ANALYSIS stage, not just
    # generation — a budget exhausted after the first chunk stops further chunk calls (and, in
    # turn, the generation loop never runs either since the same budget is already spent).
    _set_long_source_knobs(monkeypatch, threshold=5_000, chunk_chars=6_000)
    long_source = _long_text(4, para_chars=6_000)

    provider = _ScriptedProvider(["chunk one analysis"])
    result = await _run(provider, source_text=long_source, token_budget=15)

    assert len(provider.calls) == 1  # only the first chunk's complete() call was made
    assert result.analysis_text == "chunk one analysis"
    assert result.iterations == 0
    assert result.stop_reason == "token_budget"


async def test_all_chunks_empty_falls_back_to_single_whole_source_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ingest.long_source import chunk_overlap_chars, split_into_chunks

    _set_long_source_knobs(monkeypatch, threshold=5_000, chunk_chars=6_000)
    long_source = _long_text(4, para_chars=6_000)
    n_chunks = len(split_into_chunks(long_source, 6_000, chunk_overlap_chars(6_000)))
    assert n_chunks >= 2

    # Every chunk analysis call returns empty prose (no exception) → no chunk contributes any
    # text → the loop degrades to ONE whole-source complete() call, exactly like a total-failure
    # of the JSON loop's chunked analyze_source().
    provider = _ScriptedProvider(["", *([""] * (n_chunks - 1)), ANALYSIS, GEN_TWO_FILES])
    result = await _run(provider, source_text=long_source)

    assert result.converged is True
    assert len(provider.calls) == n_chunks + 2  # n_chunks empty + 1 fallback + 1 generation
    assert result.analysis_text == ANALYSIS
    # The fallback call received the WHOLE source, not a chunk fragment.
    fallback_user = provider.calls[n_chunks][1]
    assert long_source in fallback_user
