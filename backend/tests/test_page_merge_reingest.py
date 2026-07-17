"""
Feature 2 (ADR-0063 §4) — LLM body-merge on re-ingest.

Two layers:
  • unit tests for ``maybe_merge_page_body`` — merges when enabled + prior body meaningful;
    returns the NEW body when disabled / provider None / no prior body / provider failure /
    sanity-reject (degrade-safe);
  • an integration test through ``write_wiki_page`` (api_env SQLite + temp vault) proving the
    merge path runs when the target file already exists, and that a merge failure degrades to the
    new body without failing the write.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from app.ingest.provider.base import InferenceProvider, UsageAccumulator
from app.ingest.schemas import (
    Analysis,
    Message,
    PageType,
    ProviderCapabilities,
    Usage,
    WikiFrontmatter,
    WikiPage,
)

from tests.test_api import api_env  # noqa: F401


class _MergeProvider(InferenceProvider):
    """chat() streams a canned merged body (or raises). analyze/generate unused."""

    def __init__(self, merged: str | None = None, raise_exc: bool = False) -> None:
        self._merged = merged
        self._raise = raise_exc
        self.chat_calls = 0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities("local", False, False, 8192, "Merge")

    async def analyze(self, source_text: str, vault_context: str) -> Analysis:  # pragma: no cover
        raise NotImplementedError

    async def generate(  # pragma: no cover
        self, analysis: Analysis, retrieval_context: str, source_text: str = ""
    ) -> list[WikiPage]:
        raise NotImplementedError

    async def chat(self, messages: list[Message], retrieval_context: str) -> AsyncIterator[str]:
        self.chat_calls += 1
        self._record_usage(Usage(input_tokens=5, output_tokens=5, total_cost_usd=0.0))
        raise_exc = self._raise
        merged = self._merged

        async def _gen() -> AsyncIterator[str]:
            if raise_exc:
                raise RuntimeError("merge boom")
            for tok in (merged or "").split(" "):
                yield tok + " "

        return _gen()


def _set_merge(monkeypatch: pytest.MonkeyPatch, *, enabled: bool = True) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "ingest_reingest_merge_enabled", enabled)
    monkeypatch.setattr(settings, "ingest_reingest_merge_timeout_seconds", 30.0)


OLD = "Existing paragraph one about the topic. " * 4
NEW = "New paragraph two contributed by a second source. " * 4


# ── Unit: maybe_merge_page_body ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_merge_runs_when_enabled_and_prior_body_meaningful(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ingest.page_merge import maybe_merge_page_body

    _set_merge(monkeypatch, enabled=True)
    merged_body = (OLD + NEW) * 2  # long enough to pass the shrink sanity check
    provider = _MergeProvider(merged=merged_body)
    provider.bind_accumulator(UsageAccumulator())
    out = await maybe_merge_page_body(
        provider, OLD, NEW, title="Topic", origin_source="raw/sources/b.md"
    )
    assert provider.chat_calls == 1
    assert out.strip() == merged_body.strip()


@pytest.mark.asyncio
async def test_merge_skipped_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ingest.page_merge import maybe_merge_page_body

    _set_merge(monkeypatch, enabled=False)
    provider = _MergeProvider(merged="whatever")
    out = await maybe_merge_page_body(
        provider, OLD, NEW, title="Topic", origin_source="raw/sources/b.md"
    )
    assert provider.chat_calls == 0
    assert out == NEW


@pytest.mark.asyncio
async def test_merge_skipped_when_provider_none(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ingest.page_merge import maybe_merge_page_body

    _set_merge(monkeypatch, enabled=True)
    out = await maybe_merge_page_body(
        None, OLD, NEW, title="Topic", origin_source="raw/sources/b.md"
    )
    assert out == NEW


@pytest.mark.asyncio
async def test_merge_skipped_when_no_prior_body(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ingest.page_merge import maybe_merge_page_body

    _set_merge(monkeypatch, enabled=True)
    provider = _MergeProvider(merged="x")
    out = await maybe_merge_page_body(
        provider, "", NEW, title="Topic", origin_source="raw/sources/b.md"
    )
    assert provider.chat_calls == 0  # brand-new page → no merge
    assert out == NEW


@pytest.mark.asyncio
async def test_merge_degrades_on_provider_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ingest.page_merge import maybe_merge_page_body

    _set_merge(monkeypatch, enabled=True)
    provider = _MergeProvider(raise_exc=True)
    provider.bind_accumulator(UsageAccumulator())
    out = await maybe_merge_page_body(
        provider, OLD, NEW, title="Topic", origin_source="raw/sources/b.md"
    )
    assert out == NEW  # degrade-safe


@pytest.mark.asyncio
async def test_merge_rejects_truncated_body(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ingest.page_merge import maybe_merge_page_body

    _set_merge(monkeypatch, enabled=True)
    # A merged body far shorter than max(old,new) → sanity-reject → keep new.
    provider = _MergeProvider(merged="tiny")
    provider.bind_accumulator(UsageAccumulator())
    out = await maybe_merge_page_body(
        provider, OLD, NEW, title="Topic", origin_source="raw/sources/b.md"
    )
    assert out == NEW


def test_merge_system_prompt_preserves_subject_boundaries() -> None:
    # nashsu/llm_wiki ingest.ts:2792-2793 — a merge must not fold comparison subjects into the
    # main page subject, and must keep conflicting/different-subject claims separated.
    from app.ingest.page_merge import _MERGE_SYSTEM_PROMPT

    lowered = _MERGE_SYSTEM_PROMPT.lower()
    assert "preserve subject boundaries" in lowered
    assert "do not fold them into claims about the main page subject" in lowered
    assert "keep them separated" in lowered


# ── Integration through write_wiki_page ─────────────────────────────────────────────


def _wikipage(title: str, body: str, source: str) -> WikiPage:
    return WikiPage(
        title=title,
        type=PageType.CONCEPT,
        content=body,
        frontmatter=WikiFrontmatter(
            type=PageType.CONCEPT, title=title, sources=[source], lang="en"
        ),
    )


@pytest.mark.asyncio
async def test_write_wiki_page_merges_existing_body(
    api_env: dict[str, Any],  # noqa: F811 — pytest fixture param shadows the import (documented)
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ingest.writer import write_wiki_page

    _set_merge(monkeypatch, enabled=True)
    merged_marker = ("MERGED BODY combining both sources. " * 8).strip()
    provider = _MergeProvider(merged=merged_marker)
    provider.bind_accumulator(UsageAccumulator())

    # First write: brand-new page → provider NOT called (no prior body).
    await write_wiki_page(
        None,
        _wikipage("Widgets", OLD, "raw/sources/a.md"),
        "raw/sources/a.md",
        provider=provider,
    )
    assert provider.chat_calls == 0

    # Second write of the SAME slug from a new source → merge path runs.
    row = await write_wiki_page(
        None,
        _wikipage("Widgets", NEW, "raw/sources/b.md"),
        "raw/sources/b.md",
        provider=provider,
    )
    assert provider.chat_calls == 1

    abs_path = api_env["vault_root"] / row.file_path
    written = abs_path.read_text(encoding="utf-8")
    assert "MERGED BODY" in written  # the merged body landed on disk (single write, I1)


@pytest.mark.asyncio
async def test_write_wiki_page_merge_failure_degrades(
    api_env: dict[str, Any],  # noqa: F811 — pytest fixture param shadows the import (documented)
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ingest.writer import write_wiki_page

    _set_merge(monkeypatch, enabled=True)
    provider = _MergeProvider(raise_exc=True)
    provider.bind_accumulator(UsageAccumulator())

    await write_wiki_page(
        None,
        _wikipage("Gadgets", OLD, "raw/sources/a.md"),
        "raw/sources/a.md",
        provider=provider,
    )
    row = await write_wiki_page(
        None,
        _wikipage("Gadgets", NEW, "raw/sources/b.md"),
        "raw/sources/b.md",
        provider=provider,
    )
    abs_path = api_env["vault_root"] / row.file_path
    written = abs_path.read_text(encoding="utf-8")
    # Degrade-safe: the new body was written; the write never failed.
    assert NEW.strip()[:20] in written
