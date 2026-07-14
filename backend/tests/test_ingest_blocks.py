"""Regression suite for the FILE / REVIEW / LINT block parsers in app.ingest.blocks.

Ported 1:1 from nashsu/llm_wiki v0.6.3 src/lib/ingest-parse.test.ts (the
parseFileBlocks + isSafeIngestPath cases), plus added coverage for the REVIEW /
LINT parsers and the first_char_is_file_opener helper. Test names mirror the TS
`it(...)` descriptions, snake_cased.
"""

from app.ingest.blocks import (
    FileBlock,
    LintBlock,
    ReviewBlock,
    first_char_is_file_opener,
    is_safe_ingest_path,
    parse_blocks,
    parse_file_blocks,
    parse_lint_blocks,
    parse_review_blocks,
)

# ── Happy paths (parseFileBlocks — canonical shapes) ─────────────────────────


def test_extracts_a_single_well_formed_block():
    text = "\n".join(
        [
            "---FILE: wiki/concepts/rope.md---",
            "# RoPE",
            "Rotary positional embedding.",
            "---END FILE---",
        ]
    )
    result = parse_file_blocks(text)
    assert len(result.warnings) == 0
    assert len(result.files) == 1
    assert result.files[0].path == "wiki/concepts/rope.md"
    assert "# RoPE" in result.files[0].content


def test_extracts_multiple_consecutive_blocks():
    text = "\n".join(
        [
            "---FILE: wiki/entities/qwen.md---",
            "# Qwen",
            "---END FILE---",
            "",
            "---FILE: wiki/concepts/moe.md---",
            "# MoE",
            "---END FILE---",
            "",
            "---FILE: wiki/sources/paper.md---",
            "# Source summary",
            "---END FILE---",
        ]
    )
    result = parse_file_blocks(text)
    assert len(result.warnings) == 0
    assert [b.path for b in result.files] == [
        "wiki/entities/qwen.md",
        "wiki/concepts/moe.md",
        "wiki/sources/paper.md",
    ]


def test_accepts_hyphenated_paths():
    text = "\n".join(
        [
            "---FILE: wiki/concepts/multi-head-attention.md---",
            "body",
            "---END FILE---",
        ]
    )
    assert len(parse_file_blocks(text).files) == 1


def test_ignores_preamble_prose_before_the_first_block():
    text = "\n".join(
        [
            "Here are the wiki files:",
            "",
            "---FILE: wiki/concepts/foo.md---",
            "body",
            "---END FILE---",
        ]
    )
    assert len(parse_file_blocks(text).files) == 1


# ── H1: CRLF normalization ───────────────────────────────────────────────────


def test_extracts_all_blocks_when_input_uses_windows_crlf():
    text = "\r\n".join(
        [
            "---FILE: wiki/entities/qwen.md---",
            "# Qwen",
            "---END FILE---",
            "",
            "---FILE: wiki/concepts/moe.md---",
            "# MoE",
            "---END FILE---",
        ]
    )
    result = parse_file_blocks(text)
    assert len(result.warnings) == 0
    assert len(result.files) == 2
    assert [b.path for b in result.files] == [
        "wiki/entities/qwen.md",
        "wiki/concepts/moe.md",
    ]
    for b in result.files:
        assert "\r" not in b.content


def test_handles_mixed_crlf_body_with_lf_markers():
    text = "---FILE: wiki/concepts/foo.md---\nline1\r\nline2\r\n---END FILE---"
    result = parse_file_blocks(text)
    assert len(result.files) == 1
    assert result.files[0].content == "line1\nline2"


# ── H2: Stream truncation ────────────────────────────────────────────────────


def test_emits_a_warning_when_the_final_block_has_no_closer():
    text = "\n".join(
        [
            "---FILE: wiki/entities/qwen.md---",
            "# Qwen",
            "---END FILE---",
            "",
            "---FILE: wiki/concepts/moe.md---",
            "# Mixture of Exp",  # stream cut here
        ]
    )
    result = parse_file_blocks(text)
    assert len(result.files) == 1
    assert result.files[0].path == "wiki/entities/qwen.md"
    assert len(result.warnings) == 1
    assert "wiki/concepts/moe.md" in result.warnings[0]
    assert "not closed" in result.warnings[0].lower()


def test_warns_when_the_only_block_is_unclosed():
    text = "---FILE: wiki/concepts/rope.md---\n# RoPE\nIt rotates"
    result = parse_file_blocks(text)
    assert len(result.files) == 0
    assert len(result.warnings) == 1
    assert "rope.md" in result.warnings[0]


# ── H3: Marker whitespace / case variants ────────────────────────────────────


def test_accepts_end_file_with_inner_spaces():
    text = "\n".join(
        [
            "---FILE: wiki/concepts/foo.md---",
            "body",
            "--- END FILE ---",
        ]
    )
    assert len(parse_file_blocks(text).files) == 1


def test_accepts_lowercase_end_file():
    text = "\n".join(
        [
            "---FILE: wiki/concepts/foo.md---",
            "body",
            "---end file---",
        ]
    )
    assert len(parse_file_blocks(text).files) == 1


def test_accepts_file_opener_with_spaces_after_leading_dashes():
    text = "\n".join(
        [
            "--- FILE: wiki/concepts/foo.md ---",
            "body",
            "---END FILE---",
        ]
    )
    result = parse_file_blocks(text)
    assert len(result.files) == 1
    assert result.files[0].path == "wiki/concepts/foo.md"


def test_accepts_lowercase_file_opener():
    # Task-required case: `--- file:` opener is case-insensitive.
    text = "\n".join(
        [
            "--- file: wiki/concepts/foo.md ---",
            "body",
            "---end file---",
        ]
    )
    result = parse_file_blocks(text)
    assert len(result.files) == 1
    assert result.files[0].path == "wiki/concepts/foo.md"


def test_tolerates_trailing_whitespace_on_the_opener_line():
    text = "---FILE: wiki/concepts/foo.md---   \nbody\n---END FILE---"
    assert len(parse_file_blocks(text).files) == 1


def test_rejects_marker_variants_embedded_in_prose_or_list_items():
    text = "\n".join(
        [
            "---FILE: wiki/concepts/foo.md---",
            "Not to be written:",
            "- `---END FILE---` in backticks (this is prose)",
            "real content continues",
            "---END FILE---",
        ]
    )
    result = parse_file_blocks(text)
    assert len(result.files) == 1
    assert "real content continues" in result.files[0].content


# ── H5: Literal markers inside fenced code blocks ────────────────────────────


def test_treats_end_file_inside_a_fenced_code_block_as_body_text():
    text = "\n".join(
        [
            "---FILE: wiki/concepts/ingest-format.md---",
            "# Ingest Format",
            "",
            "Example of a FILE block:",
            "",
            "```plaintext",
            "---FILE: wiki/path/to/page.md---",
            "body content",
            "---END FILE---",  # inside a fence — must be ignored
            "```",
            "",
            "More explanation after the example.",
            "---END FILE---",  # the real closer
        ]
    )
    result = parse_file_blocks(text)
    assert len(result.warnings) == 0
    assert len(result.files) == 1
    assert result.files[0].path == "wiki/concepts/ingest-format.md"
    assert "```plaintext" in result.files[0].content
    assert "More explanation after the example." in result.files[0].content


def test_handles_multiple_fenced_blocks_in_one_page():
    text = "\n".join(
        [
            "---FILE: wiki/concepts/foo.md---",
            "```",
            "---END FILE---",
            "```",
            "",
            "prose",
            "",
            "~~~",
            "---END FILE---",
            "~~~",
            "",
            "more prose",
            "---END FILE---",
        ]
    )
    result = parse_file_blocks(text)
    assert len(result.files) == 1
    assert "more prose" in result.files[0].content


def test_handles_nested_length_fences_per_commonmark():
    text = "\n".join(
        [
            "---FILE: wiki/concepts/foo.md---",
            "````markdown",
            "```",
            "---END FILE---",
            "```",
            "````",
            "",
            "real content after the outer fence closes",
            "---END FILE---",
        ]
    )
    result = parse_file_blocks(text)
    assert len(result.files) == 1
    assert "real content after the outer fence closes" in result.files[0].content


def test_a_three_tick_fence_does_not_close_a_four_tick_opener():
    text = "\n".join(
        [
            "---FILE: wiki/concepts/foo.md---",
            "````",
            "```",
            "---END FILE---",  # still inside the 4-tick fence
            "```",
            "````",
            "",
            "real content",
            "---END FILE---",
        ]
    )
    result = parse_file_blocks(text)
    assert len(result.files) == 1
    assert "real content" in result.files[0].content


# ── H6: Empty path ───────────────────────────────────────────────────────────


def test_surfaces_a_warning_instead_of_silently_dropping_empty_path_blocks():
    text = "---FILE:   ---\nsome body\n---END FILE---"
    result = parse_file_blocks(text)
    assert len(result.files) == 0
    assert len(result.warnings) > 0


# ── isSafeIngestPath — what the validator accepts and rejects ────────────────


def test_accepts_canonical_wiki_paths():
    assert is_safe_ingest_path("wiki/concepts/foo.md") is True
    assert is_safe_ingest_path("wiki/index.md") is True
    assert is_safe_ingest_path("wiki/sources/some-paper.md") is True
    assert is_safe_ingest_path("wiki/entities/transformer.md") is True


def test_rejects_empty_or_whitespace_only_paths():
    assert is_safe_ingest_path("") is False
    assert is_safe_ingest_path("   ") is False
    assert is_safe_ingest_path("\t\n") is False


def test_rejects_paths_outside_wiki_no_leading_wiki_prefix():
    assert is_safe_ingest_path("notes/foo.md") is False
    assert is_safe_ingest_path("foo.md") is False
    assert is_safe_ingest_path("raw/sources/leaked.md") is False


def test_rejects_absolute_posix_paths():
    assert is_safe_ingest_path("/etc/passwd") is False
    assert is_safe_ingest_path("/Users/nash_su/.ssh/authorized_keys") is False
    assert is_safe_ingest_path("/wiki/foo.md") is False  # even with wiki/ in the path


def test_rejects_windows_absolute_paths_and_drive_letters():
    assert is_safe_ingest_path("C:/Windows/System32/config") is False
    assert is_safe_ingest_path("c:\\Users\\victim\\evil.txt") is False
    assert is_safe_ingest_path("\\Users\\victim\\evil.txt") is False
    assert is_safe_ingest_path("\\\\server\\share\\file.md") is False


def test_rejects_any_segment_exactly_equal_to_dotdot_every_position():
    assert is_safe_ingest_path("wiki/../etc/passwd") is False
    assert is_safe_ingest_path("wiki/concepts/../../etc/passwd") is False
    assert is_safe_ingest_path("wiki/..") is False
    assert is_safe_ingest_path("..") is False
    assert is_safe_ingest_path("wiki\\..\\etc\\passwd") is False


def test_does_not_reject_filenames_that_merely_contain_double_dots():
    assert is_safe_ingest_path("wiki/concepts/qwen-2.5..notes.md") is True
    assert is_safe_ingest_path("wiki/concepts/foo..bar.md") is True


def test_rejects_nul_bytes_and_control_characters():
    assert is_safe_ingest_path("wiki/concepts/foo\x00.md") is False
    assert is_safe_ingest_path("wiki/concepts/foo\nbar.md") is False
    assert is_safe_ingest_path("wiki/\x07alarm.md") is False


def test_rejects_windows_invalid_characters_in_generated_filenames():
    assert is_safe_ingest_path("wiki/concepts/Article: Why It Matters.md") is False
    assert is_safe_ingest_path('wiki/concepts/quoted"name.md') is False
    assert is_safe_ingest_path("wiki/concepts/a|b.md") is False
    assert is_safe_ingest_path("wiki/concepts/a?b.md") is False
    assert is_safe_ingest_path("wiki/concepts/a*b.md") is False
    assert is_safe_ingest_path("wiki/concepts/a<b>.md") is False


def test_rejects_windows_reserved_device_names_even_with_extensions():
    assert is_safe_ingest_path("wiki/concepts/con.md") is False
    assert is_safe_ingest_path("wiki/concepts/NUL.pdf.md") is False
    assert is_safe_ingest_path("wiki/concepts/com1.md") is False
    assert is_safe_ingest_path("wiki/concepts/LPT9.notes.md") is False
    assert is_safe_ingest_path("wiki/concepts/auxiliary.md") is True


def test_rejects_segments_ending_in_a_space_or_dot_for_windows_compatibility():
    assert is_safe_ingest_path("wiki/concepts/topic .md") is True
    assert is_safe_ingest_path("wiki/concepts/topic.") is False
    assert is_safe_ingest_path("wiki/concepts/topic ") is False
    assert is_safe_ingest_path("wiki/concepts/folder./topic.md") is False
    assert is_safe_ingest_path("wiki/concepts/folder /topic.md") is False


# ── parseFileBlocks — path-traversal guard end-to-end ───────────────────────


def test_drops_blocks_with_dotdot_paths_and_surfaces_a_warning():
    text = "\n".join(
        [
            "---FILE: wiki/concepts/legit.md---",
            "Real page.",
            "---END FILE---",
            "---FILE: ../../etc/passwd---",
            "attacker:x:0:0::/root:/bin/bash",
            "---END FILE---",
        ]
    )
    result = parse_file_blocks(text)
    assert len(result.files) == 1
    assert result.files[0].path == "wiki/concepts/legit.md"
    assert any("../../etc/passwd" in w for w in result.warnings)
    assert any("unsafe path" in w for w in result.warnings)


def test_drops_blocks_with_absolute_paths():
    text = "\n".join(
        [
            "---FILE: /etc/passwd---",
            "evil",
            "---END FILE---",
        ]
    )
    result = parse_file_blocks(text)
    assert len(result.files) == 0
    assert any("unsafe path" in w for w in result.warnings)


def test_drops_blocks_not_under_wiki():
    text = "\n".join(
        [
            "---FILE: src-tauri/src/main.rs---",
            'fn main() { panic!("injected"); }',
            "---END FILE---",
        ]
    )
    result = parse_file_blocks(text)
    assert len(result.files) == 0
    assert any("unsafe path" in w for w in result.warnings)


def test_an_llm_mixing_safe_and_unsafe_paths_writes_only_the_safe_ones():
    text = "\n".join(
        [
            "---FILE: wiki/concepts/topic-a.md---",
            "topic A page",
            "---END FILE---",
            "---FILE: ../config.json---",
            '{"hijacked": true}',
            "---END FILE---",
            "---FILE: wiki/entities/topic-b.md---",
            "topic B page",
            "---END FILE---",
        ]
    )
    result = parse_file_blocks(text)
    assert [b.path for b in result.files] == [
        "wiki/concepts/topic-a.md",
        "wiki/entities/topic-b.md",
    ]
    assert any("../config.json" in w for w in result.warnings)


# ── parse_review_blocks ──────────────────────────────────────────────────────


def test_review_block_with_options_pages_and_search():
    text = "\n".join(
        [
            "---REVIEW: missing-page | Retrieval-Augmented Generation---",
            "RAG is referenced heavily but has no page.",
            "OPTIONS: Create Page | Skip",
            "PAGES: wiki/concepts/rag.md, wiki/entities/openai.md",
            "SEARCH: retrieval augmented generation | RAG LLM architecture",
            "---END REVIEW---",
        ]
    )
    items = parse_review_blocks(text)
    assert len(items) == 1
    item = items[0]
    assert isinstance(item, ReviewBlock)
    assert item.type == "missing-page"
    assert item.title == "Retrieval-Augmented Generation"
    assert item.description == "RAG is referenced heavily but has no page."
    assert item.options == ["Create Page", "Skip"]
    assert item.pages == ["wiki/concepts/rag.md", "wiki/entities/openai.md"]
    assert item.search_queries == [
        "retrieval augmented generation",
        "RAG LLM architecture",
    ]


def test_review_block_without_options_pages_or_search_uses_defaults():
    text = "\n".join(
        [
            "---REVIEW: contradiction | Conflicting throughput claims---",
            "Page A says 10k tok/s, page B says 2k tok/s.",
            "---END REVIEW---",
        ]
    )
    items = parse_review_blocks(text)
    assert len(items) == 1
    item = items[0]
    assert item.type == "contradiction"
    assert item.description == "Page A says 10k tok/s, page B says 2k tok/s."
    assert item.options == ["Approve", "Skip"]
    assert item.pages == []
    assert item.search_queries == []


def test_review_block_unknown_type_falls_back_to_confirm():
    text = "\n".join(
        [
            "---REVIEW: wat | Something odd---",
            "Body.",
            "---END REVIEW---",
        ]
    )
    items = parse_review_blocks(text)
    assert len(items) == 1
    assert items[0].type == "confirm"


def test_review_block_type_is_case_insensitive():
    text = "\n".join(
        [
            "---REVIEW: DUPLICATE | Two pages about RoPE---",
            "Body.",
            "---END REVIEW---",
        ]
    )
    items = parse_review_blocks(text)
    assert len(items) == 1
    assert items[0].type == "duplicate"


def test_review_search_filters_empty_queries():
    text = "\n".join(
        [
            "---REVIEW: suggestion | Add a benchmark page---",
            "Body.",
            "SEARCH: mlperf inference || llm throughput benchmark",
            "---END REVIEW---",
        ]
    )
    items = parse_review_blocks(text)
    assert items[0].search_queries == ["mlperf inference", "llm throughput benchmark"]


def test_multiple_review_blocks_are_all_parsed():
    text = "\n".join(
        [
            "---REVIEW: suggestion | One---",
            "body one",
            "---END REVIEW---",
            "prose in between (ignored)",
            "---REVIEW: duplicate | Two---",
            "body two",
            "PAGES: a.md, b.md",
            "---END REVIEW---",
        ]
    )
    items = parse_review_blocks(text)
    assert [i.title for i in items] == ["One", "Two"]
    assert items[1].pages == ["a.md", "b.md"]


# ── parse_lint_blocks ────────────────────────────────────────────────────────


def test_parses_a_lint_block():
    text = "\n".join(
        [
            "---LINT: contradiction | warning | Conflicting dates---",
            "Two pages disagree on the release year.",
            "PAGES: a.md, b.md",
            "---END LINT---",
        ]
    )
    blocks = parse_lint_blocks(text)
    assert len(blocks) == 1
    block = blocks[0]
    assert isinstance(block, LintBlock)
    assert block.type == "contradiction"
    assert block.severity == "warning"
    assert block.title == "Conflicting dates"
    assert "Two pages disagree on the release year." in block.detail


def test_lint_block_unknown_type_and_severity_fall_back():
    text = "\n".join(
        [
            "---LINT: mystery | critical | Weird title---",
            "Body.",
            "---END LINT---",
        ]
    )
    blocks = parse_lint_blocks(text)
    assert len(blocks) == 1
    assert blocks[0].type == "suggestion"
    assert blocks[0].severity == "info"


def test_lint_block_info_severity_preserved():
    text = "\n".join(
        [
            "---LINT: stale | info | Outdated benchmark---",
            "Body.",
            "---END LINT---",
        ]
    )
    blocks = parse_lint_blocks(text)
    assert blocks[0].type == "stale"
    assert blocks[0].severity == "info"


# ── parse_blocks convenience + first_char_is_file_opener ─────────────────────


def test_parse_blocks_returns_files_and_reviews_and_warnings():
    text = "\n".join(
        [
            "---FILE: wiki/concepts/foo.md---",
            "body",
            "---END FILE---",
            "---FILE: ../escape.md---",
            "evil",
            "---END FILE---",
            "---REVIEW: suggestion | Add a page---",
            "body",
            "---END REVIEW---",
        ]
    )
    result = parse_blocks(text)
    assert [b.path for b in result.files] == ["wiki/concepts/foo.md"]
    assert [r.title for r in result.reviews] == ["Add a page"]
    assert any("unsafe path" in w for w in result.warnings)


def test_file_block_dataclass_shape():
    block = FileBlock(path="wiki/x.md", content="body")
    assert block.path == "wiki/x.md"
    assert block.content == "body"


def test_first_char_is_file_opener():
    assert first_char_is_file_opener("---FILE: wiki/x.md---\nbody\n---END FILE---") is True
    assert first_char_is_file_opener("--- FILE: wiki/x.md ---") is True
    assert first_char_is_file_opener("--- file: wiki/x.md ---") is True  # case-insensitive
    # Strict: leading whitespace / preamble is NOT stripped.
    assert first_char_is_file_opener("  ---FILE: wiki/x.md---") is False
    assert first_char_is_file_opener("Here you go:\n---FILE: wiki/x.md---") is False
    assert first_char_is_file_opener("") is False
