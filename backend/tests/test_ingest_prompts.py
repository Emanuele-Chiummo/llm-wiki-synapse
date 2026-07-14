"""
Unit tests for app.ingest.prompts — the block-based ingest prompt builders (ADR-0076).

These pin the PARITY-CRITICAL invariants of the nashsu/llm_wiki v0.6.3 port, above all the
wikilink-density fix: the generation prompt must mention [[wikilink]] cross-referencing
prominently and repeatedly, and the analysis prompt must carry a "Connections to Existing Wiki"
section. A regression here is the 1.6.0 link regression returning.
"""

from __future__ import annotations

from datetime import date

from app.ingest.prompts import (
    GENERATION_WIKI_TYPES,
    build_analysis_prompt,
    build_analysis_user,
    build_generation_prompt,
    build_generation_user,
    build_language_directive,
    build_review_stage_prompt,
    language_prompt_name,
    wiki_date,
)

_FIXED = date(2026, 7, 14)


# ── date + language helpers ──────────────────────────────────────────────────────


def test_wiki_date_iso() -> None:
    assert wiki_date(_FIXED) == "2026-07-14"


def test_language_prompt_name_known_unknown_and_auto() -> None:
    assert language_prompt_name("en") == "English"
    assert language_prompt_name("IT") == "Italian"
    assert language_prompt_name("xx") == "xx"  # unknown but non-blank → raw code
    assert language_prompt_name("auto") is None
    assert language_prompt_name("") is None
    assert language_prompt_name(None) is None


def test_language_directive_empty_when_no_name() -> None:
    assert build_language_directive(None) == ""


def test_language_directive_contains_name_and_preservation_rule() -> None:
    d = build_language_directive("Italian")
    assert "MANDATORY OUTPUT LANGUAGE: Italian" in d
    assert "**Italian**" in d
    assert "Preserve organization names" in d


# ── join semantics (filter-falsy) ────────────────────────────────────────────────


def test_join_drops_blank_separators_no_double_newlines_between_single_lines() -> None:
    # The analysis prompt uses "" separators that must be stripped: no "\n\n\n" runs, and no
    # leading/trailing blank line.
    p = build_analysis_prompt(source_content="x")
    assert not p.startswith("\n")
    assert not p.endswith("\n")
    assert "\n\n\n" not in p


# ── analysis prompt ──────────────────────────────────────────────────────────────


def test_analysis_prompt_has_connections_section_link_fix() -> None:
    p = build_analysis_prompt(source_content="doc")
    assert "## Connections to Existing Wiki" in p
    assert "## Key Entities" in p
    assert "## Recommendations" in p


def test_analysis_prompt_schema_purpose_index_conditional() -> None:
    bare = build_analysis_prompt(source_content="doc")
    assert "## Project Schema" not in bare
    assert "## Wiki Purpose" not in bare
    assert "## Current Wiki Index" not in bare

    full = build_analysis_prompt(
        purpose="PURPOSE-TEXT",
        index="INDEX-TEXT",
        source_content="doc",
        schema="SCHEMA-TEXT",
    )
    assert "## Project Schema" in full and "SCHEMA-TEXT" in full
    assert "## Wiki Purpose (for context)\nPURPOSE-TEXT" in full
    assert "## Current Wiki Index (for checking existing content)\nINDEX-TEXT" in full


def test_analysis_prompt_language_directive_included() -> None:
    p = build_analysis_prompt(source_content="doc", language_name="English")
    assert "MANDATORY OUTPUT LANGUAGE: English" in p


def test_analysis_user_message() -> None:
    u = build_analysis_user(source_identity="papers/foo.pdf", source_context="BODY")
    assert "**File:** papers/foo.pdf" in u
    assert "Folder context" not in u
    assert u.endswith("---\n\nBODY")
    u2 = build_analysis_user(
        source_identity="foo.pdf", source_context="BODY", folder_context="papers/energy"
    )
    assert "**Folder context:** papers/energy" in u2


# ── generation prompt — the LINK FIX ─────────────────────────────────────────────


def test_generation_prompt_wikilink_instructions_are_prominent_and_repeated() -> None:
    """THE link-regression guard: [[wikilink]] guidance must appear multiple times."""
    p = build_generation_prompt(source_filename="foo.md", today=_FIXED)
    assert p.count("[[wikilink]]") >= 2
    assert "Use [[wikilink]] syntax in the BODY for cross-references between pages" in p
    assert "If the analysis found connections to existing pages, add cross-references" in p


def test_generation_prompt_file_block_contract_and_types() -> None:
    p = build_generation_prompt(source_filename="foo.md", today=_FIXED)
    assert "---FILE: wiki/path/to/page.md---" in p
    assert "---END FILE---" in p
    assert "---REVIEW: type | Title---" in p
    # all nine base types present in the required-fields type line
    assert " | ".join(GENERATION_WIKI_TYPES) in p


def test_generation_prompt_source_filename_and_date() -> None:
    p = build_generation_prompt(source_filename="my-source.md", today=_FIXED)
    assert 'MUST include "my-source.md"' in p
    assert "Today's date is **2026-07-14**" in p
    assert "created: 2026-07-14" in p


def test_generation_prompt_summary_path_default_and_override() -> None:
    default = build_generation_prompt(source_filename="paper.pdf", today=_FIXED)
    assert "**wiki/sources/paper.md** (MUST use this exact path)" in default
    override = build_generation_prompt(
        source_filename="paper.pdf",
        source_summary_path="wiki/sources/custom-slug.md",
        today=_FIXED,
    )
    assert "**wiki/sources/custom-slug.md** (MUST use this exact path)" in override


def test_generation_prompt_schema_routing_authoritative_conditional() -> None:
    bare = build_generation_prompt(source_filename="foo.md", today=_FIXED)
    assert "## Project Schema and Routing (AUTHORITATIVE)" not in bare
    withschema = build_generation_prompt(
        source_filename="foo.md",
        schema="| thesis | wiki/thesis/ | x |",
        today=_FIXED,
    )
    assert "## Project Schema and Routing (AUTHORITATIVE)" in withschema
    assert "| thesis | wiki/thesis/ | x |" in withschema
    assert "Every generated page's frontmatter type must match the schema directory" in withschema


def test_generation_prompt_output_format_is_last_and_no_index_overview_emit() -> None:
    p = build_generation_prompt(source_filename="foo.md", today=_FIXED)
    assert "Do not generate wiki/index.md or wiki/overview.md" in p
    # Output Format must be the final major section (models weight recent instructions highest);
    # the language directive is repeated after it, so assert Output Format precedes the tail.
    assert p.index("## Output Format") > p.index("## What to generate")


def test_generation_prompt_language_directive_repeated_top_and_bottom() -> None:
    p = build_generation_prompt(source_filename="foo.md", language_name="Italian", today=_FIXED)
    assert p.count("MANDATORY OUTPUT LANGUAGE: Italian") == 2


def test_generation_user_message_forces_file_start() -> None:
    u = build_generation_user(analysis="ANALYSIS", source_context="SRC")
    assert "## Stage 1 Analysis (context only — do not repeat)" in u
    assert "ANALYSIS" in u and "SRC" in u
    assert "---FILE:" in u


# ── review stage prompt ──────────────────────────────────────────────────────────


def test_review_stage_prompt_high_signal_and_blocks_only() -> None:
    p = build_review_stage_prompt(
        source_identity="foo.md",
        analysis="A",
        source_context="C",
        generation="G",
    )
    assert "Prefer 1-5 high-signal reviews" in p
    assert "Return REVIEW blocks only. Do not output FILE blocks." in p
    assert "---REVIEW: suggestion | Precise title---" in p


def test_review_stage_prompt_trims_long_sections() -> None:
    big = "x" * 500_000
    p = build_review_stage_prompt(
        source_identity="foo.md",
        analysis=big,
        source_context=big,
        generation=big,
        max_context_chars=204_800,
    )
    assert "[... trimmed to fit context budget ...]" in p
    # each section capped at max(4000, 15% of ctx) = 30720; three sections << 500k*3
    assert len(p) < 200_000
