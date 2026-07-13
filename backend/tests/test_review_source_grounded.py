"""
Source-grounded review proposals — llm_wiki buildReviewSuggestionPrompt parity.

llm_wiki feeds the RAW source *content* (not just the analysis) into the review-suggestion
prompt, which is what lets the model quote the document ("the doc excludes X as out-of-scope")
and surface concrete in-scope/out-of-scope handoff gaps as `suggestion` items with precise,
descriptive titles. These tests lock in the two mechanisms that reproduce that on Synapse:

  1. `_trim_source_excerpt` — bounded head+tail excerpt (cap disable, passthrough, elision).
  2. `_build_propose_instruction` — includes the source excerpt section ONLY when source text is
     given, and carries the sharpened llm_wiki framing (precise descriptive title, gap+why).

Pure functions — no DB, no provider. Fast unit coverage.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.ingest.schemas import Analysis, PageType, SuggestedPage
from app.ops.review import _build_propose_instruction, _trim_source_excerpt


def _analysis() -> Analysis:
    return Analysis(
        topics=["cloud licensing"],
        entities=[],
        language="it",
        suggested_pages=[SuggestedPage(title="Cloud Licensing", type=PageType.CONCEPT)],
        summary="A strategy document about cloud licensing extraction scope.",
    )


# ── _trim_source_excerpt ──────────────────────────────────────────────────────


def test_trim_disabled_returns_empty() -> None:
    assert _trim_source_excerpt("some source text", 0) == ""
    assert _trim_source_excerpt("some source text", -5) == ""


def test_trim_passthrough_when_under_cap() -> None:
    text = "short source"
    assert _trim_source_excerpt(text, 100) == text


def test_trim_head_and_tail_with_elision() -> None:
    text = "A" * 50 + "B" * 50  # 100 chars
    out = _trim_source_excerpt(text, 30)
    # Head+tail kept, middle elided; total content bounded near the cap.
    assert out.startswith("A")
    assert out.rstrip().endswith("B")
    assert "[source trimmed]" in out
    # Both boundaries are represented (opening scope + closing exclusions).
    assert "A" in out and "B" in out


def test_trim_empty_input() -> None:
    assert _trim_source_excerpt("", 100) == ""
    assert _trim_source_excerpt("   ", 100) == ""


# ── _build_propose_instruction: source grounding ──────────────────────────────


def test_prompt_includes_source_excerpt_when_provided() -> None:
    src = (
        "Scope: raw extraction only. Out of scope: normalization, ELP reconciliation, "
        "currency conversion, Power BI reporting."
    )
    prompt = _build_propose_instruction(
        analysis=_analysis(),
        written_pages=[],
        existing_titles=["Cloud Licensing"],
        max_items=5,
        token_budget=4000,
        source_text=src,
    )
    # The raw source content is fed to the model (the key llm_wiki lever).
    assert "Source content (raw excerpt" in prompt
    assert "ELP reconciliation" in prompt  # verbatim source passage reaches the model
    # Sharpened framing markers.
    assert "PRECISE, DESCRIPTIVE page title" in prompt
    assert "in-scope/out-of-scope handoff" in prompt


def test_prompt_omits_source_section_when_no_source() -> None:
    prompt = _build_propose_instruction(
        analysis=_analysis(),
        written_pages=[],
        existing_titles=["Cloud Licensing"],
        max_items=5,
        token_budget=4000,
        source_text="",
    )
    # No empty "Source content" header when there is nothing to show (delegated route default).
    assert "Source content (raw excerpt" not in prompt
    # But the sharpened framing is still present (benefits every route).
    assert "PRECISE, DESCRIPTIVE page title" in prompt


def test_prompt_source_excerpt_is_bounded(monkeypatch) -> None:
    # A very large source must be trimmed to the configured char budget, not dumped whole.
    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "review_propose_source_chars", 200, raising=False)
    big = "X" * 10_000
    prompt = _build_propose_instruction(
        analysis=_analysis(),
        written_pages=[],
        existing_titles=[],
        max_items=5,
        token_budget=4000,
        source_text=big,
    )
    # The full 10k blob is NOT present; the elision marker proves it was trimmed.
    assert "X" * 10_000 not in prompt
    assert "[source trimmed]" in prompt


def test_prompt_with_no_analysis_includes_only_bounded_written_page_excerpts(
    monkeypatch, tmp_path
) -> None:
    """Delegated review is grounded in the exact written page ids, never a vault-wide scan."""
    from app import config as cfg

    selected = tmp_path / "wiki" / "concepts" / "selected.md"
    selected.parent.mkdir(parents=True)
    selected.write_text("SELECTED-PAGE-EVIDENCE-" + "A" * 2_000, encoding="utf-8")
    unrelated = tmp_path / "wiki" / "concepts" / "unrelated.md"
    unrelated.write_text("UNRELATED-Vault-CONTENT", encoding="utf-8")

    monkeypatch.setattr(cfg.settings, "vault_path", str(tmp_path))
    monkeypatch.setattr(cfg.settings, "review_propose_written_pages_chars", 240, raising=False)
    monkeypatch.setattr(cfg.settings, "review_propose_source_chars", 240, raising=False)
    page = SimpleNamespace(
        title="Selected Page",
        page_type="concept",
        file_path="wiki/concepts/selected.md",
    )

    prompt = _build_propose_instruction(
        analysis=None,
        written_pages=[page],
        existing_titles=["Selected Page", "Unrelated"],
        max_items=5,
        token_budget=4_000,
        source_text="RAW-SOURCE-EVIDENCE-" + "B" * 2_000,
    )

    assert "# Ingest analysis" not in prompt
    assert "SELECTED-PAGE-EVIDENCE" in prompt
    assert "UNRELATED-Vault-CONTENT" not in prompt
    assert "A" * 2_000 not in prompt
    assert "B" * 2_000 not in prompt
