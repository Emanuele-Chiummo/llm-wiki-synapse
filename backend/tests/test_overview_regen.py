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

import uuid
from typing import Any

import app.ingest.orchestrator as orch
import frontmatter
import pytest
from app.ingest.schemas import (
    Analysis,
    PageType,
    ProviderCapabilities,
    SuggestedPage,
    Usage,
)
from sqlalchemy import text as sa_text

# Reuse the shared fixtures from test_api.py (registered by conftest auto-discovery).
from tests.test_api import api_client, api_env  # noqa: F401


async def _insert_query_page(
    env: dict[str, Any],
    *,
    title: str,
    created_at: str,
    vault_id: str = "test-vault",
) -> str:
    """Seed a live `type=query` Page row (ADR-0067 D6/P1-1 Open-Questions block source)."""
    pid = str(uuid.uuid4())
    slug = title.lower().replace(" ", "-").replace("?", "").strip("-")
    fp = f"wiki/queries/{slug}.md"
    async with env["session_factory"]() as sess:
        await sess.execute(
            sa_text(
                "INSERT INTO pages "
                "(id, vault_id, file_path, title, type, content_hash, pinned, created_at, updated_at) "
                "VALUES (:id, :v, :fp, :t, 'query', 'h', 0, :ca, :ca)"
            ),
            {"id": pid, "v": vault_id, "fp": fp, "t": title, "ca": created_at},
        )
        await sess.commit()
    return pid


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
    Minimal provider whose complete() returns a fixed narrative and records ONE usage row.
    Not an InferenceProvider subclass — _update_overview only needs bind_accumulator + complete
    (ADR-0076: overview regen uses the single-turn complete() seam, not the agentic chat() loop).
    ``chat_calls`` counts provider calls regardless of seam (assertions read "called once").
    """

    def __init__(self, narrative: str) -> None:
        self.narrative = narrative
        self.chat_calls = 0
        self._acc: Any = None

    def bind_accumulator(self, accumulator: Any) -> None:
        self._acc = accumulator

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities("local", False, False, 8192, "FakeOverview")

    async def complete(self, system: str, prompt: str, *, max_tokens: int) -> str:
        self.chat_calls += 1
        if self._acc is not None:
            self._acc.add(Usage(input_tokens=10, output_tokens=10, total_cost_usd=0.0))
        return self.narrative


class _RaisingProvider(_FakeOverviewProvider):
    """complete() raises → exercises the degrade-safe path (previous overview kept)."""

    async def complete(self, system: str, prompt: str, *, max_tokens: int) -> str:
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


# ── v1.3.14: descriptive, LLM-generated overview title (F3 parity with llm_wiki) ──────


def test_overview_instruction_asks_for_descriptive_title() -> None:
    """The prompt asks for a leading `# ` title and injects the real current period (no hallucination)."""
    instr = orch._build_overview_instruction(
        analysis=None, existing_digest="x", lang="it", now_label="2026-07"
    )
    assert "# " in instr  # top-level title heading directive
    assert "2026-07" in instr  # injected period, not left to the model to guess
    assert "DESCRIPTIVE title" in instr


def test_extract_overview_title_from_h1() -> None:
    """A leading `# ` heading becomes the title and is stripped from the body."""
    title, body = orch._extract_overview_title(
        "# Procurement Analytics Wiki — Visione Progettuale (Luglio 2026)\n\nCorpo narrativo."
    )
    assert title == "Procurement Analytics Wiki — Visione Progettuale (Luglio 2026)"
    assert body == "Corpo narrativo."
    assert not body.startswith("#")


def test_extract_overview_title_fallback_without_h1() -> None:
    """No H1 → fall back to the static config title, body unchanged (backward-compatible)."""
    title, body = orch._extract_overview_title("Just a body with no heading.")
    assert title == "Overview"
    assert body == "Just a body with no heading."


# ── F3 tag cloud (current llm_wiki parity) ──────────────────────────────────────


def test_overview_instruction_asks_for_tag_cloud() -> None:
    """The prompt requests a trailing TAGS: keyword line (llm_wiki tag-cloud parity)."""
    instr = orch._build_overview_instruction(analysis=None, existing_digest="x", lang="it")
    assert "TAGS:" in instr
    assert "keyword" in instr.lower()


def test_slugify_tag_normalises() -> None:
    assert orch._slugify_tag("  ISO 27001 ") == "iso-27001"
    assert orch._slugify_tag("#Cost_Accounting") == "cost-accounting"
    assert orch._slugify_tag("AI-Act") == "ai-act"


def test_extract_overview_keyword_tags_parses_trailing_line() -> None:
    """The trailing TAGS: line is pulled into a slugified, de-duped tag list and stripped."""
    body = "Body paragraph one.\n\nBody paragraph two.\n\nTAGS: Procurement, DORA, nis2, DORA, iso 27001"
    tags, new_body = orch._extract_overview_keyword_tags(body)
    assert tags == ["procurement", "dora", "nis2", "iso-27001"]  # slugified + de-duped, order kept
    assert "TAGS:" not in new_body
    assert new_body.rstrip().endswith("Body paragraph two.")


def test_extract_overview_keyword_tags_absent_is_degrade_safe() -> None:
    """No TAGS line (older prompt / degraded model) → ([], body-unchanged)."""
    body = "Just a narrative with no tag line."
    tags, new_body = orch._extract_overview_keyword_tags(body)
    assert tags == []
    assert new_body == body


@pytest.mark.asyncio
async def test_overview_regen_uses_h1_title(
    api_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: a narrative that opens with a descriptive H1 sets the frontmatter title."""
    narrative = (
        "# Homelab Wiki — Self-Hosting Blueprint (July 2026)\n\nThe wiki covers homelab topics."
    )
    _patch_provider(monkeypatch, _FakeOverviewProvider(narrative))
    await orch._update_overview(_analysis(), ORIGIN)

    file_text = (api_env["vault_root"] / "wiki" / "overview.md").read_text(encoding="utf-8")
    meta = frontmatter.loads(file_text)
    assert meta["title"] == "Homelab Wiki — Self-Hosting Blueprint (July 2026)"
    assert "The wiki covers homelab topics." in meta.content
    # The H1 is promoted to the title, not duplicated in the body.
    assert "# Homelab Wiki" not in meta.content


@pytest.mark.asyncio
async def test_overview_regen_writes_keyword_tag_cloud(
    api_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: the narrative's trailing TAGS: line becomes the overview frontmatter tag cloud."""
    narrative = (
        "# Homelab Wiki — Self-Hosting Blueprint (July 2026)\n\n"
        "The wiki covers homelab topics.\n\n"
        "TAGS: self-hosting, docker, networking, ISO 27001, backups"
    )
    _patch_provider(monkeypatch, _FakeOverviewProvider(narrative))
    await orch._update_overview(_analysis(), ORIGIN)

    file_text = (api_env["vault_root"] / "wiki" / "overview.md").read_text(encoding="utf-8")
    meta = frontmatter.loads(file_text)
    assert meta["tags"] == ["self-hosting", "docker", "networking", "iso-27001", "backups"]
    # The TAGS line is pulled into frontmatter, not left in the rendered body.
    assert "TAGS:" not in meta.content


# ── ADR-0067 D6/P2-5: bolded thesis lead + raised tag cap ────────────────────────


def test_overview_instruction_asks_for_thesis_lead() -> None:
    """OV-A4/OV-D3: the prompt requires a bolded thesis anchor opening the body."""
    instr = orch._build_overview_instruction(analysis=None, existing_digest="x", lang="it")
    assert "**Central thesis**:" in instr
    assert "**Tesi centrale**:" in instr


def test_overview_tag_cap_raised() -> None:
    """P2-5: the overview tag cap is raised toward LLM Wiki's ~129-keyword cloud (≥120)."""
    assert orch._OVERVIEW_MAX_TAGS >= 120


def test_overview_instruction_asks_for_40_120_tags() -> None:
    """P2-5: the prompt asks for 40-120 keywords (up from 20-40)."""
    instr = orch._build_overview_instruction(analysis=None, existing_digest="x", lang="en")
    assert "40-120" in instr


def test_extract_overview_keyword_tags_keeps_more_than_50() -> None:
    """P2-5: the extractor no longer truncates at 50 — it keeps up to the raised cap."""
    keywords = [f"kw-{i}" for i in range(120)]
    body = "Body.\n\nTAGS: " + ", ".join(keywords)
    tags, _ = orch._extract_overview_keyword_tags(body)
    assert len(tags) == 120
    assert len(tags) > 50  # regression against the old _OVERVIEW_MAX_TAGS=50 truncation


# ── ADR-0067 D6/P1-1: deterministic Open-Questions closing block ─────────────────


@pytest.mark.asyncio
async def test_open_questions_block_en_and_it_localized(api_env: dict[str, Any]) -> None:
    """P1-1: block heading localizes (it → Domande Aperte; else Open Questions); newest-first."""
    await _insert_query_page(
        api_env, title="Does scale improve reasoning?", created_at="2026-07-01 10:00:00"
    )
    await _insert_query_page(
        api_env, title="Is RAG better than fine-tuning?", created_at="2026-07-02 10:00:00"
    )

    en_block = await orch._build_open_questions_block("en")
    assert en_block.splitlines()[0] == "## Open Questions"
    # Numbered [[Title]] links, newest (2026-07-02) first.
    assert "1. [[Is RAG better than fine-tuning?]]" in en_block
    assert "2. [[Does scale improve reasoning?]]" in en_block

    it_block = await orch._build_open_questions_block("it")
    assert it_block.splitlines()[0] == "## Domande Aperte"
    # Same body, only the heading differs.
    assert en_block.split("\n", 1)[1] == it_block.split("\n", 1)[1]


@pytest.mark.asyncio
async def test_open_questions_block_omitted_when_zero(api_env: dict[str, Any]) -> None:
    """P1-1: no live query pages → empty string (section omitted entirely)."""
    block = await orch._build_open_questions_block("en")
    assert block == ""


@pytest.mark.asyncio
async def test_open_questions_block_idempotent(api_env: dict[str, Any]) -> None:
    """P1-1/I1: same query set → byte-identical block on repeated builds."""
    await _insert_query_page(api_env, title="Q one?", created_at="2026-07-01 10:00:00")
    await _insert_query_page(api_env, title="Q two?", created_at="2026-07-02 10:00:00")
    first = await orch._build_open_questions_block("en")
    second = await orch._build_open_questions_block("en")
    assert first == second
    assert first  # non-empty


@pytest.mark.asyncio
async def test_overview_appends_open_questions_block_and_meta_frontmatter(
    api_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    P1-1 + P2-5 end-to-end: _update_overview appends the Open-Questions block AFTER the LLM body
    and emits related:[]/sources:[]/created/updated frontmatter (LLM Wiki overview meta shape).
    """
    await _insert_query_page(api_env, title="Open question A?", created_at="2026-07-01 10:00:00")
    await _insert_query_page(api_env, title="Open question B?", created_at="2026-07-02 10:00:00")

    narrative = (
        "# Homelab Wiki (July 2026)\n\n"
        "**Central thesis**: the wiki documents a self-hosted homelab.\n\n"
        "TAGS: self-hosting, docker"
    )
    _patch_provider(monkeypatch, _FakeOverviewProvider(narrative))
    await orch._update_overview(_analysis(), ORIGIN)

    file_text = (api_env["vault_root"] / "wiki" / "overview.md").read_text(encoding="utf-8")
    post = frontmatter.loads(file_text)

    # Open-Questions block appended after the narrative body (en heading, newest-first).
    assert "## Open Questions" in post.content
    assert "1. [[Open question B?]]" in post.content
    assert "2. [[Open question A?]]" in post.content
    # The bolded thesis lead survived into the body.
    assert "**Central thesis**:" in post.content

    # Meta-page frontmatter: related/sources empty lists + created/updated present.
    assert post["related"] == []
    assert post["sources"] == []
    assert isinstance(post["created"], str) and post["created"]
    assert isinstance(post["updated"], str) and post["updated"]
    # lang is NOT emitted (ADR-0067 D2).
    assert "lang" not in post.metadata


@pytest.mark.asyncio
async def test_overview_open_questions_omitted_when_no_query_pages(
    api_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-1: with zero query pages the overview carries NO Open-Questions heading."""
    _patch_provider(monkeypatch, _FakeOverviewProvider("# Wiki\n\nBody only.\n\nTAGS: a, b"))
    await orch._update_overview(_analysis(), ORIGIN)
    file_text = (api_env["vault_root"] / "wiki" / "overview.md").read_text(encoding="utf-8")
    assert "## Open Questions" not in file_text
    assert "## Domande Aperte" not in file_text


@pytest.mark.asyncio
async def test_overview_created_preserved_across_regen(
    api_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """P2-5: `created` is preserved across a second regeneration (only `updated` may advance)."""
    narrative = "# Wiki (July 2026)\n\n**Central thesis**: x.\n\nBody.\n\nTAGS: a, b"
    _patch_provider(monkeypatch, _FakeOverviewProvider(narrative))
    await orch._update_overview(_analysis(), ORIGIN)
    overview_path = api_env["vault_root"] / "wiki" / "overview.md"
    created_1 = frontmatter.loads(overview_path.read_text(encoding="utf-8"))["created"]

    # Force a DIFFERENT created onto the on-disk file, then regen — it must be PRESERVED.
    post = frontmatter.loads(overview_path.read_text(encoding="utf-8"))
    post["created"] = "2020-01-01"
    overview_path.write_text(frontmatter.dumps(post) + "\n", encoding="utf-8")

    _patch_provider(monkeypatch, _FakeOverviewProvider(narrative))
    await orch._update_overview(_analysis(), ORIGIN)
    created_2 = frontmatter.loads(overview_path.read_text(encoding="utf-8"))["created"]
    assert created_2 == "2020-01-01", "created must be preserved from the prior on-disk file"
    assert created_1 != "2020-01-01"  # sanity: first run set today's date


@pytest.mark.asyncio
async def test_overview_regen_idempotent_with_open_questions(
    api_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-1/I1: two regenerations with the SAME narrative + query set yield an identical file."""
    await _insert_query_page(api_env, title="Idem Q1?", created_at="2026-07-01 10:00:00")
    await _insert_query_page(api_env, title="Idem Q2?", created_at="2026-07-02 10:00:00")
    narrative = "# Wiki (July 2026)\n\n**Central thesis**: x.\n\nBody.\n\nTAGS: a, b"

    _patch_provider(monkeypatch, _FakeOverviewProvider(narrative))
    await orch._update_overview(_analysis(), ORIGIN)
    first = (api_env["vault_root"] / "wiki" / "overview.md").read_text(encoding="utf-8")

    _patch_provider(monkeypatch, _FakeOverviewProvider(narrative))
    await orch._update_overview(_analysis(), ORIGIN)
    second = (api_env["vault_root"] / "wiki" / "overview.md").read_text(encoding="utf-8")
    assert first == second, "same narrative + same query set → byte-identical overview.md"
