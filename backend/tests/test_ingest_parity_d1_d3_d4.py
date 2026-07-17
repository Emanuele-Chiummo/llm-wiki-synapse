"""
Ingest parity fixes D1 / D3 / D4 (ADR-0063 §9, nashsu/llm_wiki parity).

D1 — generation SEES the source text.
  llm_wiki threads analysis + the (budget-trimmed) full source into generation
  (ingest.ts:1000-1016). Synapse now emits a `# Source document` section in
  build_generate_prompt and threads the run's source_text through
  InferenceProvider.generate(analysis, retrieval_context, source_text).

D3 — synthesized source-summary page matches llm_wiki.
  Title `Source: <identity>`, body `# Source: <identity>\n\n<analysis text>`, on-disk path
  `wiki/sources/<stem>.md` where <identity>/<stem> derive from the origin minus the
  `raw/sources/` prefix (source-identity.ts).

D4 — index.md and log.md are graph nodes.
  A Page row is upserted for wiki/index.md (type=index) and wiki/log.md (type=log) so the
  graph (which excludes only raw/* + type:query) renders them (wiki-graph.ts:182-209).
"""

from __future__ import annotations

from typing import Any

import app.ingest.orchestrator as orch
import app.ingest.pipeline as pipeline
import app.ingest.writer as writer
import pytest
from app.config import settings
from app.ingest.provider._common import build_generate_prompt
from app.ingest.schemas import (
    Analysis,
    PageType,
    SuggestedPage,
    WikiFrontmatter,
    WikiPage,
)

# Reuse the shared SQLite api_env / api_client harness (DB + vault) from test_api.py.
from tests.test_api import api_client, api_env  # noqa: F401

# Bind the shared fixtures into this module so pytest resolves them by name. The tuple reference
# marks the imports as "used", so the fixture-param pattern in the tests below does not trip ruff
# F811 (the per-file ruff ignore other harness reusers add lives in pyproject, out of scope here).
_SHARED_FIXTURES = (api_client, api_env)

ORIGIN = "raw/sources/example.md"


def _analysis(language: str = "en", summary: str | None = "A short summary.") -> Analysis:
    return Analysis(
        topics=["topic"],
        entities=["Thing"],
        language=language,
        suggested_pages=[SuggestedPage(title="Thing", type=PageType.ENTITY)],
        summary=summary,
    )


# ── D1: build_generate_prompt emits a Source document section ─────────────────────


def test_build_generate_prompt_includes_source_document_section() -> None:
    prompt = build_generate_prompt(
        _analysis(), retrieval_context="", source_text="UNIQUE-SOURCE-BODY-12345"
    )
    assert "# Source document" in prompt
    assert "UNIQUE-SOURCE-BODY-12345" in prompt


def test_build_generate_prompt_no_source_section_when_empty() -> None:
    prompt = build_generate_prompt(_analysis(), retrieval_context="")
    assert "# Source document" not in prompt


def test_build_generate_prompt_budget_trims_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ingest_generation_source_char_budget", 50)
    long_source = "A" * 500 + "TAIL-MARKER"
    prompt = build_generate_prompt(_analysis(), retrieval_context="", source_text=long_source)
    assert "# Source document" in prompt
    # Head kept, tail dropped, explicit truncation marker present (I7 — bounded context).
    assert "TAIL-MARKER" not in prompt
    assert "truncated to fit generation budget" in prompt


def test_build_generate_prompt_budget_zero_disables_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ingest_generation_source_char_budget", 0)
    prompt = build_generate_prompt(
        _analysis(), retrieval_context="", source_text="SHOULD-NOT-APPEAR"
    )
    assert "# Source document" not in prompt
    assert "SHOULD-NOT-APPEAR" not in prompt


# ── D3: synthesized source-summary title/body format ──────────────────────────────


def test_ensure_source_summary_title_is_source_identity() -> None:
    out = pipeline._ensure_source_summary([], _analysis(), ORIGIN)
    assert len(out) == 1
    page = out[0]
    # llm_wiki: title `Source: <identity>` where identity == origin minus raw/sources/.
    assert page.title == "Source: example.md"
    assert page.type is PageType.SOURCE
    assert ORIGIN in page.frontmatter.sources


def test_ensure_source_summary_body_has_h1_and_analysis_text() -> None:
    out = pipeline._ensure_source_summary(
        [], _analysis(summary="Distinctive summary text."), ORIGIN
    )
    body = out[0].content
    assert body.startswith("# Source: example.md")
    assert "Distinctive summary text." in body


def test_ensure_source_summary_identity_strips_subfolder_prefix() -> None:
    origin = "raw/sources/reports/2024/q3.pdf"
    out = pipeline._ensure_source_summary([], _analysis(), origin)
    assert out[0].title == "Source: reports/2024/q3.pdf"
    assert out[0].content.startswith("# Source: reports/2024/q3.pdf")


def test_source_identity_helpers() -> None:
    assert writer._source_identity("raw/sources/example.md") == "example.md"
    assert writer._source_identity("raw/sources/sub/paper.pdf") == "sub/paper.pdf"
    # Windows separators + embedded marker.
    assert writer._source_identity("vault\\raw\\sources\\doc.txt") == "doc.txt"
    assert writer._source_identity_stem("raw/sources/example.md") == "example"
    assert writer._source_identity_stem("raw/sources/sub/paper.pdf") == "paper"
    assert writer._source_identity_stem("") == ""


# ── D3: write path lands a source page at wiki/sources/<stem>.md ───────────────────


@pytest.mark.asyncio
async def test_source_page_written_at_identity_stem_path(
    api_env: dict[str, Any],
) -> None:
    """A SOURCE page's on-disk path is wiki/sources/<origin-stem>.md, not the title slug."""
    fm = WikiFrontmatter(
        type=PageType.SOURCE, title="Source: example.md", sources=[ORIGIN], lang="en"
    )
    page = WikiPage(
        title="Source: example.md",
        type=PageType.SOURCE,
        content="# Source: example.md\n\nbody",
        frontmatter=fm,
    )
    row = await writer.write_wiki_page(None, page, ORIGIN)
    # Deterministic 1-source→1-page path from the origin stem (NOT `source-example-md`).
    assert row.file_path == "wiki/sources/example.md"
    assert (api_env["vault_root"] / "wiki" / "sources" / "example.md").exists()


# ── D4: index.md + log.md become graph-node Page rows ─────────────────────────────


@pytest.mark.asyncio
async def test_index_and_log_upserted_as_page_rows(
    api_env: dict[str, Any], api_client: Any
) -> None:
    """_index_index_and_log_files upserts Page(type=index) + Page(type=log) → GET /pages."""
    wiki_dir = api_env["vault_root"] / "wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)
    (wiki_dir / "index.md").write_text(
        "---\ntype: index\ntitle: Synapse Wiki Index\n---\n\n# Index\n\n- [[A]]\n",
        encoding="utf-8",
    )
    (wiki_dir / "log.md").write_text(
        "---\ntype: log\ntitle: Synapse Ingest Log\n---\n\n## 2026-07-09\n\n- indexed\n",
        encoding="utf-8",
    )

    await orch._index_index_and_log_files()

    resp = await api_client.get("/pages", params={"limit": 200})
    assert resp.status_code == 200
    items = resp.json()["items"]

    by_path = {p.get("file_path"): p for p in items}
    assert "wiki/index.md" in by_path, "index.md must be a Page row (graph node)"
    assert "wiki/log.md" in by_path, "log.md must be a Page row (graph node)"

    idx = by_path["wiki/index.md"]
    log = by_path["wiki/log.md"]
    assert (idx.get("type") or idx.get("page_type")) == "index"
    assert (log.get("type") or log.get("page_type")) == "log"


@pytest.mark.asyncio
async def test_index_and_log_upsert_is_idempotent(api_env: dict[str, Any], api_client: Any) -> None:
    """Re-running keeps a SINGLE row per aggregate file (upsert by (vault_id, file_path))."""
    wiki_dir = api_env["vault_root"] / "wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)
    (wiki_dir / "index.md").write_text(
        "---\ntype: index\ntitle: Synapse Wiki Index\n---\n\nbody\n", encoding="utf-8"
    )
    (wiki_dir / "log.md").write_text(
        "---\ntype: log\ntitle: Synapse Ingest Log\n---\n\nbody\n", encoding="utf-8"
    )

    await orch._index_index_and_log_files()
    await orch._index_index_and_log_files()

    resp = await api_client.get("/pages", params={"limit": 200})
    items = resp.json()["items"]
    assert len([p for p in items if p.get("file_path") == "wiki/index.md"]) == 1
    assert len([p for p in items if p.get("file_path") == "wiki/log.md"]) == 1


@pytest.mark.asyncio
async def test_index_and_log_missing_files_are_noop(api_env: dict[str, Any]) -> None:
    """Missing aggregate files → no-op, never raises (degrade-safe, D4/I7)."""
    # Fresh vault: index.md/log.md may not exist yet — must not raise.
    idx = api_env["vault_root"] / "wiki" / "index.md"
    log = api_env["vault_root"] / "wiki" / "log.md"
    idx.unlink(missing_ok=True)
    log.unlink(missing_ok=True)
    await orch._index_index_and_log_files()  # must not raise
