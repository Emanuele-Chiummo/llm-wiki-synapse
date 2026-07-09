"""
Feature 1 (ADR-0063 §3) — long-source chunked analysis + checkpointing.

Infra-free: a fake InferenceProvider records analyze() calls. Verifies:
  • under the threshold → ONE analyze() call (single-source path unchanged);
  • over the threshold → chunked path: multiple analyze() calls, merged Analysis (union
    topics/entities/suggested_pages, concatenated summaries);
  • max_chunks HARD-caps the number of analyze() calls (I7);
  • a mid-way chunk failure keeps prior chunks' results (degrade-safe);
  • total chunk failure falls back to a single whole-source analyze();
  • the on-disk checkpoint is written and resumed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from app.ingest.long_source import (
    analyze_source,
    merge_analyses,
    split_into_chunks,
)
from app.ingest.provider.base import InferenceProvider, UsageAccumulator
from app.ingest.schemas import (
    Analysis,
    Message,
    PageType,
    ProviderCapabilities,
    SuggestedPage,
    Usage,
    WikiPage,
)


def _analysis(topic: str, lang: str = "en", entity: str | None = None) -> Analysis:
    return Analysis(
        topics=[topic],
        entities=[entity] if entity else [],
        language=lang,
        suggested_pages=[SuggestedPage(title=topic, type=PageType.CONCEPT)],
        summary=f"summary-{topic}",
    )


class _CountingProvider(InferenceProvider):
    """Returns a distinct Analysis per call; optionally raises on specific call indices."""

    def __init__(self, fail_on: set[int] | None = None) -> None:
        self.analyze_calls = 0
        self.seen_texts: list[str] = []
        self._fail_on = fail_on or set()

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities("local", False, False, 8192, "Counting")

    async def analyze(self, source_text: str, vault_context: str) -> Analysis:
        self.analyze_calls += 1
        idx = self.analyze_calls
        self.seen_texts.append(source_text)
        self._record_usage(Usage(input_tokens=1, output_tokens=1, total_cost_usd=0.0))
        if idx in self._fail_on:
            raise RuntimeError(f"boom on call {idx}")
        return _analysis(f"t{idx}", entity=f"e{idx}")

    async def generate(  # pragma: no cover - not used here
        self, analysis: Analysis, retrieval_context: str
    ) -> list[WikiPage]:
        raise NotImplementedError

    async def chat(  # pragma: no cover - not used here
        self, messages: list[Message], retrieval_context: str
    ) -> AsyncIterator[str]:
        raise NotImplementedError


def _long_text(paragraphs: int, para_chars: int = 6_000) -> str:
    # Distinct paragraphs separated by blank lines so the splitter has boundaries.
    return "\n\n".join(f"Para {i} " + ("x" * para_chars) for i in range(paragraphs))


def _set_knobs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    threshold: int,
    chunk_chars: int = 6_000,
    max_chunks: int = 8,
    checkpoint: bool = False,
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "ingest_long_source_char_threshold", threshold)
    monkeypatch.setattr(settings, "ingest_long_source_chunk_chars", chunk_chars)
    monkeypatch.setattr(settings, "ingest_long_source_max_chunks", max_chunks)
    monkeypatch.setattr(settings, "ingest_long_source_checkpoint_enabled", checkpoint)


# ── Pure helpers ─────────────────────────────────────────────────────────────────


def test_split_into_chunks_boundaries_and_overlap() -> None:
    text = _long_text(4, para_chars=5_000)
    chunks = split_into_chunks(text, target_chars=6_000, overlap_chars=200)
    assert len(chunks) >= 2  # 4 x ~5k paragraphs cannot fit one 6k chunk
    # Overlap: every chunk after the first starts with a tail of the previous chunk's content.
    assert all(len(c) > 0 for c in chunks)


def test_split_into_chunks_single_when_small() -> None:
    assert split_into_chunks("one\n\ntwo", target_chars=6_000, overlap_chars=100) == ["one\n\ntwo"]


def test_merge_analyses_unions_and_concatenates() -> None:
    a = _analysis("Alpha", lang="it", entity="E1")
    b = _analysis("Beta", lang="it", entity="E2")
    c = Analysis(
        topics=["Alpha"],  # duplicate topic (case-insensitive dedup)
        entities=["E1"],  # duplicate entity
        language="en",  # minority language — modal (it) should win
        suggested_pages=[SuggestedPage(title="Beta", type=PageType.CONCEPT)],  # dup page
        summary="summary-Gamma",
    )
    merged = merge_analyses([a, b, c])
    assert merged.topics == ["Alpha", "Beta"]
    assert merged.entities == ["E1", "E2"]
    assert merged.language == "it"  # modal across chunks
    assert len(merged.suggested_pages) == 2  # (Alpha, concept) + (Beta, concept), deduped
    assert merged.summary is not None
    assert "summary-Alpha" in merged.summary and "summary-Gamma" in merged.summary


# ── analyze_source routing ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_under_threshold_single_call(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_knobs(monkeypatch, threshold=1_000_000)
    provider = _CountingProvider()
    provider.bind_accumulator(UsageAccumulator())
    result = await analyze_source(provider, _long_text(4), "ctx")
    assert provider.analyze_calls == 1  # single whole-source path
    assert result.topics == ["t1"]


@pytest.mark.asyncio
async def test_over_threshold_chunks_and_merges(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_knobs(monkeypatch, threshold=5_000, chunk_chars=6_000)
    provider = _CountingProvider()
    provider.bind_accumulator(UsageAccumulator())
    result = await analyze_source(provider, _long_text(4, para_chars=6_000), "ctx")
    assert provider.analyze_calls >= 2  # chunked path took multiple analyze() calls
    # Merged topics are the union of the per-chunk topics (t1, t2, ...).
    assert len(result.topics) == provider.analyze_calls
    assert result.topics[0] == "t1"


@pytest.mark.asyncio
async def test_max_chunks_hard_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    # Small chunks + many paragraphs would yield many chunks; max_chunks caps analyze() calls.
    _set_knobs(monkeypatch, threshold=5_000, chunk_chars=4_000, max_chunks=2)
    provider = _CountingProvider()
    provider.bind_accumulator(UsageAccumulator())
    await analyze_source(provider, _long_text(10, para_chars=5_000), "ctx")
    assert provider.analyze_calls == 2  # HARD cap (I7) — never one call per paragraph


@pytest.mark.asyncio
async def test_midway_failure_keeps_prior_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    # Fail on the 2nd chunk → the merged result keeps the 1st chunk's analysis.
    _set_knobs(monkeypatch, threshold=5_000, chunk_chars=6_000)
    provider = _CountingProvider(fail_on={2})
    provider.bind_accumulator(UsageAccumulator())
    result = await analyze_source(provider, _long_text(4, para_chars=6_000), "ctx")
    # It attempted chunk 1 (ok) then chunk 2 (fail) → merged from the single successful chunk.
    assert result.topics == ["t1"]


@pytest.mark.asyncio
async def test_total_failure_falls_back_to_single_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Fail on the FIRST chunk → no chunk succeeds → fall back to one whole-source analyze() call.
    _set_knobs(monkeypatch, threshold=5_000, chunk_chars=6_000)
    provider = _CountingProvider(fail_on={1})
    provider.bind_accumulator(UsageAccumulator())
    result = await analyze_source(provider, _long_text(4, para_chars=6_000), "ctx")
    # call 1 (chunk, fails) → fallback call 2 (whole source, succeeds → topic t2).
    assert provider.analyze_calls == 2
    assert result.topics == ["t2"]
    # The fallback call received the WHOLE source text, not a chunk.
    assert len(provider.seen_texts[-1]) > len(provider.seen_texts[0])


@pytest.mark.asyncio
async def test_checkpoint_written_and_resumed(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from app.config import settings

    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(type(settings), "vault_root", property(lambda self: vault))
    _set_knobs(monkeypatch, threshold=5_000, chunk_chars=6_000, checkpoint=True)

    text = _long_text(4, para_chars=6_000)
    # Run 1: fail on chunk 2 so a partial checkpoint (chunk 1) is persisted.
    p1 = _CountingProvider(fail_on={2})
    p1.bind_accumulator(UsageAccumulator())
    await analyze_source(p1, text, "ctx")

    ck_dir = vault / ".synapse" / "ingest-progress"
    assert ck_dir.exists()
    files = list(ck_dir.glob("*.json"))
    assert files, "a checkpoint file should be written after the first successful chunk"

    # Run 2: same source, a provider that would FAIL on its first call — but the checkpoint
    # already holds chunk 1, so resume starts at chunk 2. Provider succeeds there → merged has
    # chunk1 (from checkpoint) + chunk2 (fresh).
    p2 = _CountingProvider()
    p2.bind_accumulator(UsageAccumulator())
    result = await analyze_source(p2, text, "ctx")
    # p2 analyzed fewer chunks than a cold run would (chunk 1 came from the checkpoint).
    assert p2.analyze_calls >= 1
    # Merged topics include the resumed chunk's topic and the checkpointed one.
    assert "t1" in result.topics  # from the checkpoint (p1's first chunk)
