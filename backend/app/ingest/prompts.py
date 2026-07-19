"""
Provider-neutral ingest prompt builders — the block-based pipeline (ADR-0076).

A faithful Python port of nashsu/llm_wiki v0.6.3's ``buildAnalysisPrompt`` /
``buildGenerationPrompt`` / ``buildReviewSuggestionPrompt`` / ``buildLanguageDirective``
(src/lib/ingest.ts, output-language.ts). The prompts ARE the behavior: the markdown-analysis
+ FILE/REVIEW-block generation contract is what restores wikilink density (the 1.6.0 JSON
scaffold buried the single ``[[wikilink]]`` instruction; here it is prominent and repeated, and
the analysis carries a dedicated "## Connections to Existing Wiki" section — see
docs/reference/LLMWIKI-CORE-LOGIC-v0.6.3.md §1.4).

No model id / key / endpoint here (I6) — these are pure string builders shared by the
orchestrated providers (Ollama, API) and, via the same text, the delegated CLI system prompt.
Wired into the loop/providers in a later step; this module imports nothing from the app.

PARITY NOTE — section joining: llm_wiki assembles each prompt as ``[...].filter(Boolean).join(
"\\n")``. ``filter(Boolean)`` drops empty strings, so blank-line array entries are stripped and
sections are single-newline separated (blank lines survive only INSIDE multi-line entries). We
replicate this exactly via :func:`_join` so the model sees the same bytes.
"""

from __future__ import annotations

from datetime import date

# The nine base generation page types, in nashsu/llm_wiki wiki-page-types.ts source order.
# The schema.md "Page Types" table may add custom types (goal, habit, character, …); routing is
# resolved by app.wiki.schema, not here — this constant only feeds the frontmatter `type` hint.
GENERATION_WIKI_TYPES: tuple[str, ...] = (
    "source",
    "entity",
    "concept",
    "comparison",
    "query",
    "synthesis",
    "thesis",
    "methodology",
    "finding",
)

# Shared normative "Other rules" — the link-critical guidance (prominent [[wikilink]] cross-refs,
# subject boundaries, naming). SINGLE SOURCE consumed by BOTH the orchestrated generation prompt
# and the delegated-CLI guidance so the link-density fix cannot drift between the two paths (a
# contract test asserts both carry these). Do not re-bury the wikilink lines (the 1.6.0 regression).
_OTHER_RULES: tuple[str, ...] = (
    "- Use [[wikilink]] syntax in the BODY for cross-references between pages. Write EVERY "
    "wikilink target as the destination page's bare kebab-case slug (its filename without "
    "`.md`): [[cloud-financial-operations]], NOT [[Cloud Financial Operations]]. When you want "
    "readable prose, use the piped form [[slug|Display Text]] "
    "(e.g. [[effective-licensing-position|Effective Licensing Position]]) — the left side MUST "
    "stay the slug or the link will not resolve.",
    "- Every subject you link SHOULD have a page: prefer linking to a page you are creating in "
    "this same batch or one that already exists in the index. If a concept, entity, or method is "
    "worth linking, it is worth creating.",
    "- If you include images, use wiki-root-relative paths such as "
    "`media/source-slug/image.png`; never output absolute filesystem paths.",
    "- Preserve subject boundaries: when a source discusses multiple "
    "entities/models/products/methods, keep claims, evaluations, limitations, benchmark "
    "results, and recommendations attached to the exact subject they describe.",
    "- Do not merge or generalize a claim about one subject into another subject's page "
    "solely because they share terms (for example context window size, benchmark name, "
    "dataset, architecture, or feature name).",
    "- If a page needs to mention another subject for comparison, write it explicitly as "
    "a comparison and cite which source/frontmatter `sources` entry supports that "
    "statement.",
    "- Use kebab-case filenames",
    "- Derive filenames from the page title in the mandatory output language, but short "
    "proper nouns and technical identifiers take precedence: preserve names such as "
    "OpenAI, GPT-5, Transformer, CLIP, ImageNet, PyTorch, CUDA, GitHub, arXiv, React, "
    "LanceDB, AnyTXT, MinerU, model names, dataset names, tool names, and code "
    "identifiers in their standard original form. Do not put raw URLs, citation strings, "
    "or full paper titles directly into file paths; convert surrounding descriptive prose "
    "to a safe readable title. For Chinese/Japanese/Korean prose titles, keep readable "
    "CJK characters in the filename instead of translating the slug to English.",
    "- Follow the analysis recommendations on what to emphasize",
    "- If the analysis found connections to existing pages, add cross-references",
)

# ISO-639-1 → display name for the MANDATORY OUTPUT LANGUAGE directive. Extend as needed; an
# unknown/None code yields no directive (the caller may instead pass a resolved display name).
_LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "it": "Italian",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "nl": "Dutch",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "ru": "Russian",
    "ar": "Arabic",
    "hi": "Hindi",
}


def wiki_date(today: date | None = None) -> str:
    """Return ``YYYY-MM-DD`` for *today* (port of currentWikiDate; defaults to the local date)."""
    return (today or date.today()).isoformat()


def language_prompt_name(code: str | None) -> str | None:
    """
    Map an ISO-639-1 *code* (as stored in ``vault_state.output_language``) to a display name for
    the language directive. Returns None for a missing/blank/"auto" code (→ no directive) and
    falls back to the raw code for an unknown but non-blank value.
    """
    if not code:
        return None
    normalized = code.strip().lower()
    if not normalized or normalized == "auto":
        return None
    return _LANGUAGE_NAMES.get(normalized, code.strip())


def build_language_directive(language_name: str | None) -> str:
    """
    The "⚠️ MANDATORY OUTPUT LANGUAGE" block (port of buildLanguageDirective). Returns "" when
    *language_name* is None so the caller's :func:`_join` drops it (no directive).
    """
    if not language_name:
        return ""
    return "\n".join(
        (
            f"## ⚠️ MANDATORY OUTPUT LANGUAGE: {language_name}",
            "",
            f"Write surrounding natural-language prose in **{language_name}**.",
            f"All generated prose, including prose titles and section headings, must be in "
            f"{language_name}.",
            "Do not translate, transliterate, or describe proper nouns and technical identifiers "
            "unless the source already uses a well-established localized form.",
            "Preserve organization names, product names, model names, dataset names, tool/library "
            "names, acronyms, code identifiers, file names, URLs, paper titles, citation strings, "
            "and technical terms that have no widely-used localized equivalent in their standard "
            "original form.",
            f"The source material or wiki content may be in a different language; use it as "
            f"evidence, but keep generated prose in {language_name}.",
            "This language rule overrides weaker style instructions, but it does not override the "
            "proper-noun and technical-identifier preservation rule above.",
        )
    )


def _join(parts: tuple[str, ...] | list[str]) -> str:
    """Join *parts* with "\\n", dropping empties — port of ``[...].filter(Boolean).join("\\n")``."""
    return "\n".join(p for p in parts if p)


def _trim_long_text(text: str, cap: int) -> str:
    """Head-trim *text* to *cap* chars with an explicit truncation marker (never silent — I7)."""
    if cap <= 0 or len(text) <= cap:
        return text
    return text[:cap].rstrip() + "\n\n[... trimmed to fit context budget ...]"


# ── Analysis (stage 1) ───────────────────────────────────────────────────────────


def build_analysis_prompt(
    *,
    purpose: str = "",
    index: str = "",
    source_content: str = "",
    schema: str = "",
    language_name: str | None = None,
) -> str:
    """Port of buildAnalysisPrompt (ingest.ts:2046). Free-markdown analysis, not JSON."""
    return _join(
        (
            "You are an expert research analyst. Read the source document and produce a "
            "structured analysis.",
            "Do not output chain-of-thought, hidden reasoning, or a thinking transcript. Reason "
            "internally and write only the concise final analysis.",
            "",
            build_language_directive(language_name),
            "",
            "Your analysis should cover:",
            "",
            "## Key Entities",
            "List people, organizations, products, datasets, tools mentioned. For each:",
            "- Name and type",
            "- Role in the source (central vs. peripheral)",
            "- Whether it likely already exists in the wiki (check the index)",
            "",
            "## Key Concepts",
            "List theories, methods, techniques, phenomena. For each:",
            "- Name and brief definition",
            "- Why it matters in this source",
            "- Whether it likely already exists in the wiki",
            "",
            "## Main Arguments & Findings",
            "- What are the core claims or results?",
            "- What evidence supports them?",
            "- How strong is the evidence?",
            "- Which named subject is each claim about? Do not transfer claims, limits, or "
            "evaluations from one entity/model/product/method to another just because they share "
            "keywords.",
            "",
            "## Connections to Existing Wiki",
            "- What existing pages does this source relate to?",
            "- Does it strengthen, challenge, or extend existing knowledge?",
            "",
            "## Contradictions & Tensions",
            "- Does anything in this source conflict with existing wiki content?",
            "- Are there internal tensions or caveats?",
            "",
            "## Recommendations",
            "- What wiki pages should be created or updated?",
            "- If the project schema (below) defines page types beyond entity/concept (e.g. goal, "
            "habit, reflection, finding, decision, meeting), and the source genuinely contains "
            "matching content, recommend pages of those types — name the type explicitly. Only "
            "when the source actually supports it; never invent goals/habits/journal entries that "
            "aren't in the source.",
            "- What should be emphasized vs. de-emphasized?",
            "- Any open questions worth flagging for the user?",
            "",
            "Be thorough but concise. Focus on what's genuinely important.",
            "",
            "If a folder context is provided, use it as a hint for categorization — the folder "
            "structure often reflects the user's organizational intent (e.g., 'papers/energy' "
            "suggests the file is an energy-related paper).",
            "",
            (
                "## Project Schema (page types available — map source content to schema-defined "
                f"types when it fits)\n{schema}"
                if schema
                else ""
            ),
            f"## Wiki Purpose (for context)\n{purpose}" if purpose else "",
            (f"## Current Wiki Index (for checking existing content)\n{index}" if index else ""),
        )
    )


def build_analysis_user(
    *, source_identity: str, source_context: str, folder_context: str = ""
) -> str:
    """User message for the analysis stage (ingest.ts:970)."""
    header = f"Analyze this source document:\n\n**File:** {source_identity}"
    if folder_context:
        header += f"\n**Folder context:** {folder_context}"
    return f"{header}\n\n---\n\n{source_context}"


# ── Generation (stage 2) ─────────────────────────────────────────────────────────


def build_generation_prompt(
    *,
    schema: str = "",
    purpose: str = "",
    index: str = "",
    source_filename: str,
    overview: str = "",
    source_summary_path: str | None = None,
    source_content: str = "",
    language_name: str | None = None,
    today: date | None = None,
) -> str:
    """
    Port of buildGenerationPrompt (ingest.ts:2107). Emits the FILE/REVIEW-block contract with
    the schema routing table AUTHORITATIVE and the wikilink instructions prominent (the link
    fix). *source_content* only decides whether a language directive appears — the source text
    itself is threaded in the user message, not here.
    """
    day = wiki_date(today)
    source_base = source_filename.rsplit(".", 1)[0] if "." in source_filename else source_filename
    summary_path = source_summary_path or f"wiki/sources/{source_base}.md"
    lang = build_language_directive(language_name)
    types_line = " | ".join(GENERATION_WIKI_TYPES)

    schema_block = (
        _join(
            (
                "## Project Schema and Routing (AUTHORITATIVE)",
                schema,
                "",
                "Use this schema as the primary routing rule for page types and directories.",
                "If it defines custom folders or distinctions (for example people, technologies, "
                "organizations, methods, or cases), write pages into those schema-defined folders "
                "instead of forcing them into wiki/entities/ or wiki/concepts/.",
                "Use wiki/entities/ and wiki/concepts/ only when the schema does not provide a "
                "more specific destination.",
                "Every generated page's frontmatter type must match the schema directory used in "
                "its FILE path.",
            )
        )
        if schema
        else ""
    )

    return _join(
        (
            "You are a wiki maintainer. Based on the analysis provided, generate wiki files.",
            "Do not output chain-of-thought, hidden reasoning, or explanatory preamble. Reason "
            "internally and output only the requested FILE/REVIEW blocks.",
            "",
            lang,
            "",
            "## IMPORTANT: Source File",
            f"The original source file is: **{source_filename}**",
            "All wiki pages generated from this source MUST include this filename in their "
            "frontmatter `sources` field.",
            f"Today's date is **{day}**. Use this exact date for all new `created`, `updated`, "
            "and wiki/log.md ingest dates.",
            "",
            schema_block,
            "",
            "## What to generate",
            "",
            f"1. A source summary page at **{summary_path}** (MUST use this exact path)",
            "2. Entity or schema-defined typed pages — create ONE page per DISTINCT named thing "
            "the analysis lists (each specific system, API, service, role, tool, script, template, "
            "dataset, standard, organization, or person). Prefer the SPECIFIC subject over a "
            "generic umbrella: make a page for `AWS Cost Explorer API` and a separate page for the "
            "cross-account IAM role, NOT a single catch-all `AWS` page. Prefer schema-defined "
            "directories when present; otherwise use wiki/entities/.",
            "3. Concept or schema-defined typed pages — one page per DISTINCT idea, method, "
            "technique, pattern, or abstraction the analysis identifies (do not merge separable "
            "concepts into one page). Prefer schema-defined directories when present; otherwise "
            "use wiki/concepts/.",
            "4. Query pages (type=query, wiki/queries/) for the open questions, contradictions, "
            "limitations, and assumptions the source and the analysis surface — phrase each title "
            "as the question. Create one per genuinely open issue; never invent questions the "
            "source does not raise.",
            "5. Comparison pages (type=comparison, wiki/comparisons/) ONLY when the source "
            "explicitly compares commensurable subjects or gives directly comparable evidence.",
            "6. Synthesis pages (type=synthesis, wiki/synthesis/) ONLY when the source integrates "
            "multiple claims or findings into a cross-cutting conclusion.",
            "Do not generate wiki/index.md, wiki/overview.md, or wiki/log.md. The application "
            "maintains aggregate navigation and the ingest log separately (one entry is appended "
            "to wiki/log.md automatically for every page written) so large wikis are never "
            "rewritten through model output and the log is never at risk of a malformed entry.",
            "",
            "## Frontmatter Rules (CRITICAL — parser is strict)",
            "",
            "Every page begins with a YAML frontmatter block. Format rules, in order of "
            "importance:",
            "",
            "1. The VERY FIRST line of the file MUST be exactly `---` (three hyphens, nothing "
            "else).",
            "   Do NOT wrap the file in a ```yaml ... ``` code fence.",
            "   Do NOT prefix it with a `frontmatter:` key or any other line.",
            "2. Each frontmatter line is a `key: value` pair on its own line.",
            "3. The frontmatter ends with another `---` line on its own.",
            "4. The next line after the closing `---` is the start of the page body.",
            "5. Arrays use the standard YAML inline form `[a, b, c]` (no outer brackets around "
            "each item).",
            "   Wikilinks belong in the BODY only — never write `related: [[a]], [[b]]` (invalid "
            "YAML);",
            "   write `related: [a, b]` with bare slugs.",
            "",
            "Required fields and types:",
            f"  • type     — one of the known types ({types_line}), or a custom type explicitly "
            "defined by the project schema",
            '  • title    — string (quote it if it contains a colon, e.g. `title: "Foo: Bar"`)',
            f"  • created  — {day} for new pages (YYYY-MM-DD, no quotes)",
            f"  • updated  — {day} for new pages (same as created)",
            "  • tags     — array of bare strings: `tags: [microbiology, ai]`",
            "  • related  — array of bare wiki page slugs: `related: [foo, bar-baz]`. Do NOT "
            "include",
            "               `wiki/`, `.md`, or `[[…]]` here — slugs only.",
            f'  • sources  — array of source filenames; MUST include "{source_filename}".',
            "",
            "Concrete example of a complete, parseable page (everything between the two `---` "
            "lines",
            "is the frontmatter; the heading and prose below are the body):",
            "",
            "    ---",
            "    type: entity",
            "    title: Example Entity",
            f"    created: {day}",
            f"    updated: {day}",
            "    tags: [example, demo]",
            "    related: [related-slug-1, related-slug-2]",
            f'    sources: ["{source_filename}"]',
            "    ---",
            "",
            "    # Example Entity",
            "",
            "    Body content goes here. Use [[wikilink]] syntax in the body for cross-references.",
            "",
            "Other rules:",
            *_OTHER_RULES,
            "",
            "## Review block types",
            "",
            "After all FILE blocks, optionally emit REVIEW blocks for anything that needs human "
            "judgment:",
            "",
            "- contradiction: the analysis found conflicts with existing wiki content",
            "- duplicate: an entity/concept might already exist under a different name in the "
            "index",
            "- missing-page: an important concept is referenced but has no dedicated page",
            "- suggestion: ideas for further research, related sources to look for, or "
            "connections worth exploring",
            "",
            "Only create reviews for things that genuinely need human input. Don't create trivial "
            "reviews.",
            "",
            "## OPTIONS allowed values (only these predefined labels):",
            "",
            "- contradiction: OPTIONS: Create Page | Skip",
            "- duplicate: OPTIONS: Create Page | Skip",
            "- missing-page: OPTIONS: Create Page | Skip",
            "- suggestion: OPTIONS: Create Page | Skip",
            "",
            "The user also has a 'Deep Research' button (auto-added by the system) that triggers "
            "web search.",
            "Do NOT invent custom option labels. Only use 'Create Page' and 'Skip'.",
            "",
            "For suggestion and missing-page reviews, the SEARCH field must contain 2-3 web "
            "search queries",
            "(keyword-rich, specific, suitable for a search engine — NOT titles or sentences). "
            "Example:",
            "  SEARCH: automated technical debt detection AI generated code | software quality "
            "metrics LLM code generation | static analysis tools agentic software development",
            "",
            f"## Wiki Purpose\n{purpose}" if purpose else "",
            (
                f"## Current Wiki Index (preserve all existing entries, add new ones)\n{index}"
                if index
                else ""
            ),
            (
                f"## Current Overview (update this to reflect the new source)\n{overview}"
                if overview
                else ""
            ),
            "",
            # Output-format section MUST be last — models weight recent instructions highest.
            "## Output Format (MUST FOLLOW EXACTLY — this is how the parser reads your response)",
            "",
            "Your ENTIRE response consists of FILE blocks followed by optional REVIEW blocks. "
            "Nothing else.",
            "",
            "FILE block template:",
            "```",
            "---FILE: wiki/path/to/page.md---",
            "(complete file content with YAML frontmatter)",
            "---END FILE---",
            "```",
            "",
            "REVIEW block template (optional, after all FILE blocks):",
            "```",
            "---REVIEW: type | Title---",
            "Description of what needs the user's attention.",
            "OPTIONS: Create Page | Skip",
            "PAGES: wiki/page1.md, wiki/page2.md",
            "SEARCH: query 1 | query 2 | query 3",
            "---END REVIEW---",
            "```",
            "",
            "## Output Requirements (STRICT — deviations will cause parse failure)",
            "",
            "1. The FIRST character of your response MUST be `-` (the opening of `---FILE:`).",
            '2. DO NOT output any preamble such as "Here are the files:", "Based on the '
            'analysis...", or any introductory prose.',
            "3. DO NOT echo or restate the analysis — that was stage 1's job. Your job is to emit "
            "FILE blocks.",
            "4. DO NOT output markdown tables, bullet lists, or headings outside of FILE/REVIEW "
            "blocks.",
            "5. DO NOT output any trailing commentary after the last `---END FILE---` or "
            "`---END REVIEW---`.",
            "6. Between blocks, use only blank lines — no prose.",
            "7. FILE block prose (body, explanations, descriptions, section text) must use the "
            "mandatory output language specified below. Preserve proper nouns, acronyms, model "
            "names, dataset names, tool/library names, code identifiers, URLs, file names, "
            "citation strings, paper titles, and technical terms with no widely-used localized "
            "equivalent in their standard original form, including in page names and section "
            "headings.",
            "",
            "If you start with anything other than `---FILE:`, the entire response will be "
            "discarded.",
            "",
            # Repeat the language directive last so it wins the most-recent-instruction tiebreak.
            "---",
            "",
            lang,
        )
    )


def build_generation_user(*, analysis: str, source_context: str) -> str:
    """User message for the generation stage (ingest.ts:1004)."""
    return _join(
        (
            "## Stage 1 Analysis (context only — do not repeat)",
            analysis,
            "",
            "## Source Context",
            source_context,
            "",
            "Begin your response with `---FILE:` now.",
        )
    )


# ── Dedicated review stage ───────────────────────────────────────────────────────


def build_review_stage_prompt(
    *,
    purpose: str = "",
    index: str = "",
    source_identity: str,
    analysis: str,
    source_context: str,
    generation: str,
    max_context_chars: int = 204_800,
    language_name: str | None = None,
) -> str:
    """Port of buildReviewSuggestionPrompt (ingest.ts:2276). REVIEW blocks only, 1-5 high-signal."""
    section_cap = max(4_000, max_context_chars * 15 // 100)
    index_cap = max(3_000, section_cap * 80 // 100)
    return _join(
        (
            "You are identifying high-value follow-up research items for a personal wiki.",
            "Do not output chain-of-thought, hidden reasoning, or explanatory preamble.",
            "",
            build_language_directive(language_name),
            "",
            "Your job is NOT to generate wiki pages. The wiki page generation already happened.",
            "Output only REVIEW blocks for unresolved knowledge gaps that deserve human attention "
            "or Deep Research.",
            "",
            "Create REVIEW blocks only for genuinely useful follow-up work:",
            "- missing-page: an important entity/concept is referenced but still lacks a "
            "dedicated page",
            "- suggestion: a research question, source type, or comparison that would materially "
            "improve the wiki",
            "- contradiction: a conflict or tension that requires user judgment",
            "- duplicate: likely duplicate pages/names that need user review",
            "",
            "Prefer 1-5 high-signal reviews. If there is nothing worth reviewing, output nothing.",
            "For suggestion and missing-page reviews, include a SEARCH line with 2-3 keyword-rich "
            "web search queries separated by ` | `.",
            "Use only these options: OPTIONS: Create Page | Skip",
            "",
            "REVIEW block template:",
            "```",
            "---REVIEW: suggestion | Precise title---",
            "Concise description of the gap and why it matters.",
            "OPTIONS: Create Page | Skip",
            "PAGES: wiki/page1.md, wiki/page2.md",
            "SEARCH: query 1 | query 2 | query 3",
            "---END REVIEW---",
            "```",
            "",
            "Return REVIEW blocks only. Do not output FILE blocks. Do not wrap the response in "
            "markdown fences.",
            "",
            f"## Wiki Purpose\n{purpose}" if purpose else "",
            (f"## Current Wiki Index\n{_trim_long_text(index, index_cap)}" if index else ""),
            "",
            f"## Source\n{source_identity}",
            "",
            "## Stage 1 Analysis",
            _trim_long_text(analysis, section_cap),
            "",
            "## Source Context",
            _trim_long_text(source_context, section_cap),
            "",
            "## Generated Wiki Output",
            _trim_long_text(generation, section_cap),
        )
    )


# ── Delegated CLI generation guidance ────────────────────────────────────────────


def build_delegated_generation_guidance(
    *,
    schema: str = "",
    source_filename: str,
    language_name: str | None = None,
    today: date | None = None,
    source_summary_path: str | None = None,
) -> str:
    """
    Content-generation guidance for the DELEGATED (CLI agent) route (ADR-0076). The agent writes
    pages directly via its file/MCP tools, so this OMITS the FILE/REVIEW-block output-format
    contract of build_generation_prompt — but it shares the same authoritative schema-routing and
    the SAME ``_OTHER_RULES`` (prominent [[wikilink]] cross-referencing, subject boundaries,
    naming). This is the delegated half of the link-density fix: it replaces the old buried-wikilink
    GENERATION_SCAFFOLD so the CLI path (the 1:1 E2E) links pages as densely as nashsu/llm_wiki.
    A contract test asserts this and build_generation_prompt carry the same normative link lines.
    """
    day = wiki_date(today)
    source_base = source_filename.rsplit(".", 1)[0] if "." in source_filename else source_filename
    summary_path = source_summary_path or f"wiki/sources/{source_base}.md"
    types_line = " | ".join(GENERATION_WIKI_TYPES)
    lang = build_language_directive(language_name)

    # Full schema block only when the caller passes schema.md explicitly; the pipeline instead
    # prepends the project schema via the shared ingest context, so it passes schema="" and relies
    # on the always-on routing-adherence instruction below.
    schema_block = (
        _join(("## Project Schema and Routing (AUTHORITATIVE)", schema)) if schema else ""
    )

    return _join(
        (
            "You are a wiki maintainer running as an autonomous agent. Using your file-writing "
            "tools, create the wiki pages this source supports — write each page directly to its "
            "path under wiki/. Reason internally; do not narrate.",
            "",
            lang,
            "",
            "## IMPORTANT: Source File",
            f"The original source file is: **{source_filename}**",
            "Every wiki page generated from this source MUST include this filename in its "
            "frontmatter `sources` field.",
            f"Today's date is **{day}**. Use this exact date for all new `created`, `updated`, "
            "and wiki/log.md ingest dates.",
            "",
            schema_block,
            "",
            "## Routing (AUTHORITATIVE)",
            "Route each page by the project schema's Page Types table: a page's frontmatter type "
            "MUST match the directory in its path, and schema-defined folders (people, "
            "technologies, methods, cases, …) take precedence over wiki/entities/ and "
            "wiki/concepts/. Use wiki/entities/ or wiki/concepts/ only when the schema offers no "
            "more specific destination.",
            "",
            "## What to write",
            "",
            f"1. A source summary page at **{summary_path}** (use this exact path).",
            "2. Entity or schema-defined typed pages for the key named things in the source. "
            "Prefer schema-defined directories when present; otherwise use wiki/entities/.",
            "3. Concept or schema-defined typed pages for the key ideas, methods, and "
            "abstractions. Prefer schema-defined dirs when present; otherwise use wiki/concepts/.",
            "4. Append a wiki/log.md entry (format: ## [YYYY-MM-DD] ingest | Title).",
            "Do not create or rewrite wiki/index.md or wiki/overview.md — the application "
            "maintains aggregate navigation separately.",
            "",
            "## Frontmatter (every page)",
            f"YAML frontmatter between `---` fences with: type (one of {types_line}, or a "
            "schema-defined custom type), title, created/updated = today, tags (bare strings), "
            "related (bare page slugs — no wiki/, no .md, no [[…]]), and sources (MUST include "
            f'"{source_filename}"). Wikilinks belong in the BODY, never in frontmatter.',
            "",
            "Other rules:",
            *_OTHER_RULES,
            "",
            # Repeat the language directive last (most-recent-instruction tie-breaker).
            lang,
        )
    )
