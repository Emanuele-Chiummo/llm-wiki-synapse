"""Regression suite for app.ingest.sanitize.

Ported 1:1 from nashsu/llm_wiki v0.6.3 src/lib/ingest-sanitize.test.ts, plus a few
edge cases for the individual rule functions. Test names mirror the TS `it(...)`
descriptions, snake_cased.
"""

from app.ingest.sanitize import (
    add_missing_opening_frontmatter_fence,
    repair_wikilink_lists_in_frontmatter,
    sanitize_ingested_file_content,
    strip_frontmatter_key_prefix,
    strip_outer_code_fence,
)

# ── sanitizeIngestedFileContent ──────────────────────────────────────────────


def test_returns_clean_content_unchanged():
    input_ = "---\ntype: entity\ntitle: Foo\n---\n\n# Foo\n\nbody"
    assert sanitize_ingested_file_content(input_) == input_


def test_strips_a_yaml_wrapped_document_and_leaves_the_frontmatter_block_standard():
    input_ = "```yaml\n---\ntype: entity\ntitle: Accumulibacter\n---\n\n# Body\n```"
    out = sanitize_ingested_file_content(input_)
    assert out == "---\ntype: entity\ntitle: Accumulibacter\n---\n\n# Body"


def test_strips_a_md_wrapped_document():
    input_ = "```md\n---\ntype: x\n---\nbody\n```"
    assert sanitize_ingested_file_content(input_) == "---\ntype: x\n---\nbody"


def test_strips_a_markdown_wrapped_document():
    input_ = "```markdown\n---\ntype: x\n---\nbody\n```"
    assert sanitize_ingested_file_content(input_) == "---\ntype: x\n---\nbody"


def test_strips_a_bare_wrapped_document_no_lang():
    input_ = "```\n---\ntype: x\n---\nbody\n```"
    assert sanitize_ingested_file_content(input_) == "---\ntype: x\n---\nbody"


def test_does_not_strip_a_non_fence_wrapped_document_with_a_body_code_block():
    input_ = "---\ntype: x\n---\n\n# Heading\n\n```js\nconsole.log('hi')\n```\n\nmore body"
    assert sanitize_ingested_file_content(input_) == input_


def test_does_not_strip_a_partially_fenced_document():
    input_ = "```yaml\n---\ntype: x\n---\nbody"
    assert sanitize_ingested_file_content(input_) == input_


def test_strips_a_leading_frontmatter_key_prefix_when_followed_by_a_real_block():
    input_ = "frontmatter:\n---\ntype: entity\ntitle: LSTM\n---\n\n# Body"
    assert sanitize_ingested_file_content(input_) == (
        "---\ntype: entity\ntitle: LSTM\n---\n\n# Body"
    )


def test_repairs_a_missing_opening_frontmatter_fence_when_the_closing_fence_is_present():
    input_ = '\n\ntype: entity\ntitle: "Foo: Bar"\nsources: [foo.pdf]\n---\n\n# Foo\n\nBody'
    assert sanitize_ingested_file_content(input_) == (
        '---\ntype: entity\ntitle: "Foo: Bar"\nsources: [foo.pdf]\n---\n\n# Foo\n\nBody'
    )


def test_does_not_invent_frontmatter_when_a_body_line_only_looks_like_metadata():
    input_ = "title: A research question\n\n# Notes\n\nBody"
    assert sanitize_ingested_file_content(input_) == input_


def test_does_not_strip_the_word_frontmatter_when_it_appears_mid_document():
    input_ = "---\ntype: x\n---\n\nThe frontmatter: of this doc is above."
    assert sanitize_ingested_file_content(input_) == input_


def test_repairs_an_invalid_wikilink_list_inside_frontmatter():
    input_ = "---\ntype: entity\nrelated: [[a]], [[b]], [[c]]\n---\n\nbody"
    assert sanitize_ingested_file_content(input_) == (
        '---\ntype: entity\nrelated: ["[[a]]", "[[b]]", "[[c]]"]\n---\n\nbody'
    )


def test_does_not_touch_a_single_key_wikilink():
    input_ = "---\nrelated: [[a]]\n---\nbody"
    assert sanitize_ingested_file_content(input_) == input_


def test_does_not_touch_wikilink_style_text_that_appears_in_the_body():
    input_ = "---\ntype: x\n---\n\nrelated: [[a]], [[b]] in body prose"
    assert sanitize_ingested_file_content(input_) == input_


def test_composes_all_three_repairs_on_a_real_corpus_shaped_input():
    input_ = "```yaml\nfrontmatter:\n---\ntype: entity\nrelated: [[a]], [[b]]\n---\n\n# Body\n```"
    out = sanitize_ingested_file_content(input_)
    assert out == '---\ntype: entity\nrelated: ["[[a]]", "[[b]]"]\n---\n\n# Body'


# ── CRLF coverage ────────────────────────────────────────────────────────────


def test_strip_outer_code_fence_handles_crlf_losslessly():
    # Rule 1's regexes are `\r?\n`-aware, so fence stripping is CRLF-safe.
    input_ = "```yaml\r\n---\r\ntype: x\r\n---\r\nbody\r\n```"
    assert strip_outer_code_fence(input_) == "---\r\ntype: x\r\n---\r\nbody"


def test_repair_wikilink_lists_reproduces_llmwiki_crlf_plus4_quirk():
    # Pinned 1:1 with llm_wiki: rule 4's reconstruction slices `content[:m.index + 4]`
    # (ingest-sanitize.ts:167-171), which assumes an LF `---\n` opener. On CRLF
    # frontmatter the `\n` is dropped and the first payload char is duplicated. This
    # is a faithful reproduction of an upstream latent bug — the Synapse orchestrator
    # must normalize CRLF -> LF BEFORE calling the sanitizer (parse_file_blocks already
    # normalizes; the sanitizer, like llm_wiki, does not).
    assert (
        repair_wikilink_lists_in_frontmatter("---\r\ntype: x\r\n---\r\nbody")
        == "---\rtype: xx\r\n---\r\nbody"
    )


# ── individual rule functions (exposed for testability) ─────────────────────


def test_strip_outer_code_fence_is_a_noop_without_a_fence():
    input_ = "---\ntype: x\n---\nbody"
    assert strip_outer_code_fence(input_) == input_


def test_strip_frontmatter_key_prefix_is_a_noop_without_the_prefix():
    input_ = "---\ntype: x\n---\nbody"
    assert strip_frontmatter_key_prefix(input_) == input_


def test_add_missing_opening_frontmatter_fence_is_a_noop_when_already_fenced():
    input_ = "---\ntype: x\n---\nbody"
    assert add_missing_opening_frontmatter_fence(input_) == input_


def test_add_missing_opening_frontmatter_fence_stops_at_a_heading():
    # A '#' heading before any '---' means we do NOT invent frontmatter.
    input_ = "type: entity\n# Heading\n---\nbody"
    assert add_missing_opening_frontmatter_fence(input_) == input_


def test_repair_wikilink_lists_is_a_noop_without_frontmatter():
    input_ = "# Body\nrelated: [[a]], [[b]]"
    assert repair_wikilink_lists_in_frontmatter(input_) == input_
