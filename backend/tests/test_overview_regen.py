"""
F3 auto-maintained Overview tests (nashsu/llm_wiki parity).

Mirrors llm_wiki: overview.md is a SINGLE note, fully REGENERATED (overwrite, not append) on
every ingest via ONE bounded provider call (I6/I7), then indexed as a Page(type="overview") so
GET /pages returns it and the nav "Overview" section shows count 1.

Coverage:
  T-OV-1  regeneration OVERWRITES overview.md (does not append the previous body).
  T-OV-2  overview.md is indexed as a Page(type="overview") → appears in GET /pages.
  T-OV-3  degrade-safe: provider error keeps the previous overview.md; ingest still succeeds;
          no exception escapes _update_overview.
  T-OV-4  bounded: EXACTLY ONE provider chat() call per regeneration (no loop).
  T-OV-5  frontmatter is Obsidian-valid (I5): type: overview + title.

Reuses the shared api_env / api_client SQLite harness from test_api.py.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import app.ingest.orchestrator as orch
import frontmatter
import pytest
from app.ingest.schemas import (
    Analysis,
    Message,
    PageType,
    ProviderCapabilities,
    SuggestedPage,
    Usage,
)

# Reuse the shared fixtures from test_api.py (registered by conftest auto-discovery).
from tests.test_api import api_client, api_env  # noqa: F401

ORIGIN = "raw/sources/x.md"


def _analysis() -> Analysis:
    return Analysis(
        topics=["homelab", "truenas"],
        entities=["RTX 3060"],
        language="en",
        suggested_pages=[SuggestedPage(title="P", type=PageType.CONCEPT)],
        summary="A homelab wiki about self-hosting.",
    )


def test_overview_instruction_explicit_language_directive() -> None:
    """F3 / G-P1-8: a detected analysis language forces the overview into that language."""
    it_analysis = Analysis(
        topics=["licensing"],
        entities=["IBM"],
        language="it",
        suggested_pages=[SuggestedPage(title="P", type=PageType.CONCEPT)],
        summary="Wiki sul procurement.",
    )
    instr = orch._build_overview_instruction(
        analysis=it_analysis, existing_digest="x", lang=it_analysis.language
    )
    assert "MANDATORY OUTPUT LANGUAGE: Italian (it)" in instr
    assert "Do NOT translate to English" in instr


def test_overview_instruction_fallback_language_directive() -> None:
    """Delegated route (lang unknown): overview matches purpose+pages language, not default EN."""
    instr = orch._build_overview_instruction(analysis=None, existing_digest="x")
    assert "SAME LANGUAGE" in instr
    assert "Do NOT default to English" in instr


class _FakeOverviewProvider:
    """
    Minimal provider whose chat() streams a fixed narrative and records ONE usage row.
    Not an InferenceProvider subclass — _update_overview only needs bind_accumulator + chat.
    """

    def __init__(self, narrative: str) -> None:
        self.narrative = narrative
        self.chat_calls = 0
        self._acc: Any = None

    def bind_accumulator(self, accumulator: Any) -> None:
        self._acc = accumulator

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities("local", False, False, 8192, "FakeOverview")

    async def chat(
        self, messages: list[Message], retrieval_context: str
    ) -> AsyncIterator[str]:
        self.chat_calls += 1
        if self._acc is not None:
            self._acc.add(Usage(input_tokens=10, output_tokens=10, total_cost_usd=0.0))

        narrative = self.narrative

        async def _gen() -> AsyncIterator[str]:
            yield narrative

        return _gen()


class _RaisingProvider(_FakeOverviewProvider):
    """chat() raises → exercises the degrade-safe path (previous overview kept)."""

    async def chat(
        self, messages: list[Message], retrieval_context: str
    ) -> AsyncIterator[str]:
        self.chat_calls += 1
        raise RuntimeError("provider boom")


def _patch_provider(monkeypatch: pytest.MonkeyPatch, provider: Any) -> None:
    """Route _update_overview's provider resolution to *provider* (bypasses provider_config)."""

    async def fake_resolve() -> tuple[Any, Any]:
        class _Row:
            token_budget = 3_000

        return provider, _Row()

    monkeypatch.setattr(orch, "_resolve_overview_provider", fake_resolve)


@pytest.mark.asyncio
async def test_overview_regen_overwrites_not_appends(
    api_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-OV-1: two regenerations → the file contains ONLY the latest narrative (overwrite)."""
    wiki_dir = api_env["vault_root"] / "wiki"
    overview_path = wiki_dir / "overview.md"

    _patch_provider(monkeypatch, _FakeOverviewProvider("FIRST narrative body."))
    await orch._update_overview(_analysis(), ORIGIN)
    first = overview_path.read_text(encoding="utf-8")
    assert "FIRST narrative body." in first

    _patch_provider(monkeypatch, _FakeOverviewProvider("SECOND narrative body."))
    await orch._update_overview(_analysis(), ORIGIN)
    second = overview_path.read_text(encoding="utf-8")

    # Full overwrite: the new body replaced the old one (NOT append).
    assert "SECOND narrative body." in second
    assert "FIRST narrative body." not in second
    # Exactly one frontmatter block (I5 — no duplicated/stacked frontmatter): the file starts
    # with a single opening fence and carries one `type: overview` line.
    assert second.startswith("---")
    assert second.count("type: overview") == 1


@pytest.mark.asyncio
async def test_overview_indexed_as_page_and_listed(
    api_env: dict[str, Any], api_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-OV-2 + T-OV-5: overview.md becomes a Page(type=overview) surfaced by GET /pages."""
    _patch_provider(monkeypatch, _FakeOverviewProvider("The wiki covers homelab topics."))
    await orch._update_overview(_analysis(), ORIGIN)

    resp = await api_client.get("/pages", params={"limit": 200})
    assert resp.status_code == 200
    items = resp.json()["items"]

    overviews = [p for p in items if p.get("file_path") == "wiki/overview.md"]
    assert len(overviews) == 1, "overview.md must appear exactly once in GET /pages"
    ov = overviews[0]
    # Frontend routes type=="overview" → Overview bucket (count 1).
    assert ov.get("type") == "overview" or ov.get("page_type") == "overview"

    # I5: file frontmatter is Obsidian-valid (type + title).
    file_text = (api_env["vault_root"] / "wiki" / "overview.md").read_text(encoding="utf-8")
    meta = frontmatter.loads(file_text).metadata
    assert meta.get("type") == "overview"
    assert isinstance(meta.get("title"), str) and meta["title"]


@pytest.mark.asyncio
async def test_overview_regen_idempotent_single_page_row(
    api_env: dict[str, Any], api_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-OV-2 (idempotency): re-regenerating keeps a SINGLE overview Page row (upsert)."""
    _patch_provider(monkeypatch, _FakeOverviewProvider("v1"))
    await orch._update_overview(_analysis(), ORIGIN)
    _patch_provider(monkeypatch, _FakeOverviewProvider("v2"))
    await orch._update_overview(_analysis(), ORIGIN)

    resp = await api_client.get("/pages", params={"limit": 200})
    items = resp.json()["items"]
    overviews = [p for p in items if p.get("file_path") == "wiki/overview.md"]
    assert len(overviews) == 1, "upsert by (vault_id, file_path) — never a duplicate row"


@pytest.mark.asyncio
async def test_overview_degrade_safe_on_provider_error(
    api_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-OV-3: provider error keeps the previous overview.md; no exception escapes."""
    wiki_dir = api_env["vault_root"] / "wiki"
    overview_path = wiki_dir / "overview.md"

    # Seed a good overview first.
    _patch_provider(monkeypatch, _FakeOverviewProvider("GOOD previous overview."))
    await orch._update_overview(_analysis(), ORIGIN)
    good = overview_path.read_text(encoding="utf-8")
    assert "GOOD previous overview." in good

    # Now a provider that raises — must NOT raise, and must KEEP the previous file.
    raising = _RaisingProvider("unused")
    _patch_provider(monkeypatch, raising)
    await orch._update_overview(_analysis(), ORIGIN)  # must not raise

    kept = overview_path.read_text(encoding="utf-8")
    assert kept == good, "previous overview.md must be preserved on provider failure"
    assert raising.chat_calls == 1  # it was attempted exactly once


@pytest.mark.asyncio
async def test_overview_bounded_single_provider_call(
    api_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-OV-4: exactly ONE provider chat() call per regeneration (no loop)."""
    provider = _FakeOverviewProvider("bounded body")
    _patch_provider(monkeypatch, provider)
    await orch._update_overview(_analysis(), ORIGIN)
    assert provider.chat_calls == 1


@pytest.mark.asyncio
async def test_overview_no_provider_configured_keeps_previous(
    api_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    I6: with NO provider configured, _update_overview does not fabricate a backend — it keeps
    whatever overview.md exists and does not raise.
    """
    async def resolve_none() -> None:
        return None

    monkeypatch.setattr(orch, "_resolve_overview_provider", resolve_none)
    # No overview.md yet → nothing to index, and no crash.
    await orch._update_overview(_analysis(), ORIGIN)
    assert not (api_env["vault_root"] / "wiki" / "overview.md").exists()
