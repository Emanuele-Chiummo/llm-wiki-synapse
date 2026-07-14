"""Regression suite for app.wiki.schema (schema-driven page-type routing).

Ports every case from nashsu/llm_wiki v0.6.3 src/lib/wiki-schema.test.ts (test
names mirror the TS ``it(...)`` descriptions, snake_cased) and adds Synapse-flavored
cases for the five onboarding templates' Page Types tables (research / reading /
personal / business) plus edge cases for the individual behaviors.

The one intentional divergence from the TS assertions: routing values are the BARE
subdir segment (``"sources"``) instead of llm_wiki's wiki-relative form
(``"wiki/sources"``) — see the module docstring's "Representation note". Error
messages are still rendered in the ``wiki/…`` form, so the TS message assertions
port verbatim.
"""

from app.wiki.schema import (
    BASE_TYPE_DIRS,
    BASE_WIKI_TYPES,
    parse_page_type_routing,
    subdir_for_type,
    validate_page_routing,
)

# The TS fixture (wiki-schema.test.ts:7-17), verbatim.
SCHEMA = """# Wiki Schema

## Page Types

| Type | Directory | Purpose |
| ---- | --------- | ------- |
| source | wiki/sources/ | Source summaries |
| concept | wiki/concepts/ | Ideas |
| method | wiki/methods/ | Methods |
| overview | wiki/ | Top-level overview |
"""


# ── parseWikiSchemaRouting (wiki-schema.test.ts:19-56) ────────────────────────


def test_parse_extracts_type_directories_from_the_page_types_table():
    routing = parse_page_type_routing(SCHEMA)
    assert routing == {
        "source": "sources",
        "concept": "concepts",
        "method": "methods",
        "overview": "",  # "wiki/" (root) → "" in Synapse's bare representation
    }


def test_parse_ignores_unrelated_markdown_tables_outside_the_page_types_section():
    md = "\n".join(
        [
            "# Wiki Schema",
            "",
            "| Name | Directory |",
            "| ---- | --------- |",
            "| draft | wiki/drafts/ |",
            "",
            "## Page Types",
            "",
            "| Type | Directory | Purpose |",
            "| ---- | --------- | ------- |",
            "| concept | wiki/concepts/ | Ideas |",
            "",
            "## Examples",
            "",
            "| Type | Directory |",
            "| ---- | --------- |",
            "| person | wiki/people/ |",
        ]
    )
    assert parse_page_type_routing(md) == {"concept": "concepts"}


# ── validateWikiPageRouting (wiki-schema.test.ts:58-98) ───────────────────────


def test_validate_reports_a_mismatch_between_frontmatter_type_and_schema_directory():
    routing = parse_page_type_routing(SCHEMA)
    ok, reason = validate_page_routing("source", "wiki/concepts/flash-attention.md", routing)
    assert ok is False
    assert reason is not None
    assert 'type "source" must be under "wiki/sources/"' in reason


def test_validate_allows_custom_schema_types_routed_by_the_table():
    routing = parse_page_type_routing(SCHEMA)
    assert validate_page_routing("method", "wiki/methods/retrieval.md", routing) == (True, None)


def test_validate_does_not_enforce_pages_without_a_parseable_type():
    routing = parse_page_type_routing(SCHEMA)
    # The caller extracts the frontmatter type; a page with none yields "" / blank.
    assert validate_page_routing("", "wiki/concepts/no-type.md", routing) == (True, None)
    assert validate_page_routing("   ", "wiki/concepts/no-type.md", routing) == (True, None)


# ── Error condition 2: dir routed to a different type (wiki-schema.ts:84-89) ───


def test_validate_reports_a_directory_routed_to_a_different_type_than_declared():
    routing = parse_page_type_routing(SCHEMA)
    # "dataset" is not routed, so error-condition-1 is skipped; wiki/sources/ is
    # routed to "source", so error-condition-2 fires.
    ok, reason = validate_page_routing("dataset", "wiki/sources/data.md", routing)
    assert ok is False
    assert reason is not None
    assert 'must use type "source"' in reason
    assert 'but found "dataset"' in reason


# ── overview / wiki-root row ──────────────────────────────────────────────────


def test_validate_overview_page_at_the_wiki_root_is_valid():
    routing = parse_page_type_routing(SCHEMA)
    assert validate_page_routing("overview", "wiki/overview.md", routing) == (True, None)


def test_validate_overview_type_outside_the_wiki_root_is_rejected():
    routing = parse_page_type_routing(SCHEMA)
    ok, reason = validate_page_routing("overview", "wiki/concepts/foo.md", routing)
    assert ok is False
    assert reason is not None
    assert 'must be under "wiki/"' in reason


# ── Parser edge cases ─────────────────────────────────────────────────────────


def test_parse_returns_empty_when_there_is_no_page_types_heading():
    assert parse_page_type_routing("# Wiki Schema\n\nNo page types here.\n") == {}
    assert parse_page_type_routing("") == {}


def test_parse_drops_rows_whose_directory_is_not_under_wiki():
    md = "\n".join(
        [
            "## Page Types",
            "",
            "| type | dir | purpose |",
            "| ---- | --- | ------- |",
            "| person | people/ | not under wiki |",
            "| widget | wiki-notes/ | wiki-prefixed but not a wiki/ subdir |",
            "| concept | wiki/concepts/ | ok |",
        ]
    )
    assert parse_page_type_routing(md) == {"concept": "concepts"}


def test_parse_ignores_header_and_separator_rows():
    md = "\n".join(
        [
            "## Page Types",
            "| Type | Directory |",
            "| --- | --- |",
            "| entity | wiki/entities/ |",
        ]
    )
    assert parse_page_type_routing(md) == {"entity": "entities"}


def test_parse_ignores_rows_with_fewer_than_two_cells():
    md = "\n".join(
        [
            "## Page Types",
            "| entity |",  # only one cell after split[1:-1]
            "| concept | wiki/concepts/ |",
        ]
    )
    assert parse_page_type_routing(md) == {"concept": "concepts"}


def test_parse_later_rows_override_earlier_for_the_same_type():
    # wiki-schema.ts:41 assigns typeDirs[type] in table order → last write wins.
    md = "\n".join(
        [
            "## Page Types",
            "| type | dir |",
            "| ---- | --- |",
            "| concept | wiki/concepts/ |",
            "| concept | wiki/ideas/ |",
        ]
    )
    assert parse_page_type_routing(md) == {"concept": "ideas"}


def test_parse_preserves_type_case_it_does_not_lowercase():
    # wiki-schema.ts:34 trims the cell but never lowercases it; :41 stores it as-is.
    md = "\n".join(
        [
            "## Page Types",
            "| Type | Directory |",
            "| ---- | --------- |",
            "| Source | wiki/sources/ |",
        ]
    )
    assert parse_page_type_routing(md) == {"Source": "sources"}


def test_parse_page_types_heading_is_case_insensitive_and_any_level():
    md = "\n".join(
        [
            "### page types",
            "| type | dir |",
            "| ---- | --- |",
            "| entity | wiki/entities/ |",
        ]
    )
    assert parse_page_type_routing(md) == {"entity": "entities"}


def test_parse_section_spans_subheadings_but_stops_at_same_or_higher_heading():
    md = "\n".join(
        [
            "## Page Types",
            "| type | dir |",
            "| ---- | --- |",
            "| source | wiki/sources/ |",
            "### Extra types",  # deeper (level 3) → stays inside the section
            "| concept | wiki/concepts/ |",
            "## Other Section",  # same level (2) → ends the section
            "| method | wiki/methods/ |",
        ]
    )
    assert parse_page_type_routing(md) == {"source": "sources", "concept": "concepts"}


# ── subdir_for_type ───────────────────────────────────────────────────────────


def test_subdir_for_type_prefers_routing_then_base_then_type_name():
    routing = {"concept": "ideas", "person": "people"}
    assert subdir_for_type("concept", routing) == "ideas"  # routing wins
    assert subdir_for_type("person", routing) == "people"  # custom, routed
    assert subdir_for_type("entity", routing) == "entities"  # BASE_TYPE_DIRS
    assert subdir_for_type("finding", routing) == "findings"  # base, pluralised
    assert subdir_for_type("thesis", routing) == "thesis"  # base, unchanged
    assert subdir_for_type("technologies", routing) == "technologies"  # custom fallback == type


def test_subdir_for_type_routing_overrides_the_base_default():
    assert subdir_for_type("entity", {"entity": "people"}) == "people"


def test_subdir_for_type_root_routing_returns_empty_string():
    assert subdir_for_type("overview", {"overview": ""}) == ""


# ── Constants ─────────────────────────────────────────────────────────────────


def test_base_wiki_types_match_generation_wiki_types_order():
    assert BASE_WIKI_TYPES == (
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
    assert len(BASE_WIKI_TYPES) == 9


def test_base_type_dirs_cover_every_base_type_and_match_synapse_and_llmwiki():
    assert set(BASE_TYPE_DIRS) == set(BASE_WIKI_TYPES)
    # Synapse's app/ingest/schemas.py _TYPE_DIR for the original six.
    assert BASE_TYPE_DIRS["entity"] == "entities"
    assert BASE_TYPE_DIRS["concept"] == "concepts"
    assert BASE_TYPE_DIRS["source"] == "sources"
    assert BASE_TYPE_DIRS["query"] == "queries"
    assert BASE_TYPE_DIRS["comparison"] == "comparisons"
    assert BASE_TYPE_DIRS["synthesis"] == "synthesis"
    # llm_wiki's three research types.
    assert BASE_TYPE_DIRS["thesis"] == "thesis"
    assert BASE_TYPE_DIRS["methodology"] == "methodology"
    assert BASE_TYPE_DIRS["finding"] == "findings"


# ── Synapse template Page Types tables ────────────────────────────────────────

RESEARCH_SCHEMA = """## Page Types

| type | directory | purpose |
| ---- | --------- | ------- |
| source | wiki/sources/ | Source summaries |
| entity | wiki/entities/ | Named things |
| concept | wiki/concepts/ | Ideas |
| comparison | wiki/comparisons/ | Comparisons |
| synthesis | wiki/synthesis/ | Cross-source synthesis |
| query | wiki/queries/ | Saved answers |
| thesis | wiki/thesis/ | Hypotheses |
| methodology | wiki/methodology/ | Methods |
| finding | wiki/findings/ | Findings |
"""

READING_SCHEMA = """## Page Types

| type | directory | purpose |
| ---- | --------- | ------- |
| source | wiki/sources/ | Sources |
| character | wiki/characters/ | Characters |
| theme | wiki/themes/ | Themes |
| plot-thread | wiki/plot-threads/ | Plot threads |
| chapter | wiki/chapters/ | Chapter notes |
"""

PERSONAL_SCHEMA = """## Page Types

| type | directory | purpose |
| ---- | --------- | ------- |
| goal | wiki/goals/ | Goals |
| habit | wiki/habits/ | Habits |
| reflection | wiki/reflections/ | Reflections |
| journal | wiki/journal/ | Journal |
"""

BUSINESS_SCHEMA = """## Page Types

| type | directory | purpose |
| ---- | --------- | ------- |
| meeting | wiki/meetings/ | Meetings |
| decision | wiki/decisions/ | Decisions |
| project | wiki/projects/ | Projects |
| stakeholder | wiki/stakeholders/ | Stakeholders |
"""


def test_parse_research_template_adds_thesis_methodology_finding():
    routing = parse_page_type_routing(RESEARCH_SCHEMA)
    assert routing["thesis"] == "thesis"
    assert routing["methodology"] == "methodology"
    assert routing["finding"] == "findings"
    # Base types are still present alongside the research-specific ones.
    assert routing["concept"] == "concepts"
    assert routing["source"] == "sources"


def test_validate_research_finding_page_ok_and_misplaced_thesis_rejected():
    routing = parse_page_type_routing(RESEARCH_SCHEMA)
    assert validate_page_routing("finding", "wiki/findings/f1.md", routing) == (True, None)
    ok, reason = validate_page_routing("thesis", "wiki/concepts/h1.md", routing)
    assert ok is False
    assert reason is not None
    assert 'type "thesis" must be under "wiki/thesis/"' in reason


def test_parse_reading_template_adds_character_theme_plot_thread_chapter():
    routing = parse_page_type_routing(READING_SCHEMA)
    assert routing == {
        "source": "sources",
        "character": "characters",
        "theme": "themes",
        "plot-thread": "plot-threads",
        "chapter": "chapters",
    }


def test_validate_reading_hyphenated_custom_type_routes():
    routing = parse_page_type_routing(READING_SCHEMA)
    # A hyphenated custom type (plot-thread) is a valid identifier and routes.
    assert validate_page_routing("plot-thread", "wiki/plot-threads/rise.md", routing) == (
        True,
        None,
    )
    ok, reason = validate_page_routing("theme", "wiki/characters/ahab.md", routing)
    assert ok is False
    assert reason is not None
    assert 'type "theme" must be under "wiki/themes/"' in reason


def test_parse_personal_template_goal_habit_reflection_journal():
    routing = parse_page_type_routing(PERSONAL_SCHEMA)
    assert routing == {
        "goal": "goals",
        "habit": "habits",
        "reflection": "reflections",
        "journal": "journal",
    }


def test_validate_personal_goal_page_is_routed():
    routing = parse_page_type_routing(PERSONAL_SCHEMA)
    assert validate_page_routing("goal", "wiki/goals/ship-1-7.md", routing) == (True, None)


def test_parse_business_template_meeting_decision_project_stakeholder():
    routing = parse_page_type_routing(BUSINESS_SCHEMA)
    assert routing == {
        "meeting": "meetings",
        "decision": "decisions",
        "project": "projects",
        "stakeholder": "stakeholders",
    }


def test_validate_business_decision_page_ok_and_wrong_dir_rejected():
    routing = parse_page_type_routing(BUSINESS_SCHEMA)
    assert validate_page_routing("decision", "wiki/decisions/adr-1.md", routing) == (True, None)
    ok, reason = validate_page_routing("meeting", "wiki/projects/x.md", routing)
    assert ok is False
    assert reason is not None
    assert 'type "meeting" must be under "wiki/meetings/"' in reason


# ── Path-normalisation tolerance ──────────────────────────────────────────────


def test_validate_tolerates_backslashes_and_leading_slashes_in_the_path():
    routing = parse_page_type_routing(SCHEMA)
    assert validate_page_routing("method", "\\wiki\\methods\\retrieval.md", routing) == (
        True,
        None,
    )
    assert validate_page_routing("method", "/wiki/methods/retrieval.md", routing) == (True, None)


def test_validate_with_empty_routing_never_flags_anything():
    # No table → nothing to enforce; every page passes.
    assert validate_page_routing("entity", "wiki/entities/foo.md", {}) == (True, None)
    assert validate_page_routing("whatever", "wiki/anything/foo.md", {}) == (True, None)
