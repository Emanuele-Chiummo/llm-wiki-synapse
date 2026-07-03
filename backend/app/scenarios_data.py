"""
Vault scenario preset data (R7-1).

Built-in presets that the POST /scenarios/{id}/apply endpoint writes to vault/purpose.md
and vault/schema.md. Each preset provides sensible English defaults for a common vault type.

Long description strings are intentionally not line-wrapped — ruff E501 is suppressed
per-file so that the text content reads naturally.
"""

from __future__ import annotations

# ruff: noqa: E501

SCENARIOS: list[dict[str, str]] = [
    {
        "id": "research",
        "name": "Research",
        "description": "A knowledge base for structured academic or professional research — literature tracking, source review, synthesis, and open questions.",
        "purpose_md": (
            "# Vault Purpose — Research\n\n"
            "## Goal\n\n"
            "Build a structured, citable knowledge base from academic papers, reports, and primary sources.\n\n"
            "## Key Questions\n\n"
            "- What does the existing literature say about my research topic?\n"
            "- Which sources are most authoritative and frequently cited?\n"
            "- What gaps or contradictions exist in the field?\n"
            "- What is my working hypothesis and how does the evidence support or challenge it?\n\n"
            "## Scope\n\n"
            "**In scope:** Academic papers, grey literature, primary data, expert commentary.\n"
            "**Out of scope:** Informal blog posts, social media, unverified sources.\n\n"
            "## Thesis\n\n"
            "<!-- State the working hypothesis or research claim here. -->\n"
        ),
        "schema_md": (
            "# Synapse Vault Schema — Research\n\n"
            "Required frontmatter fields for every wiki page:\n\n"
            "| Field | Type | Required | Notes |\n"
            "|-------|------|----------|-------|\n"
            "| `type` | string | yes | entity, concept, source, query, synthesis, comparison |\n"
            "| `title` | string | yes | Human-readable page title |\n"
            "| `sources` | list[string] | no | Source file paths or DOI/URL for traceability |\n"
            "| `tags` | list[string] | no | Domain, methodology, or theme tags |\n\n"
            "Wikilink style: `[[PageTitle]]` (Obsidian-compatible, I5).\n\n"
            "Preferred tags: `#literature-review`, `#primary-source`, `#methodology`, `#open-question`.\n"
        ),
    },
    {
        "id": "reading",
        "name": "Reading",
        "description": "A personal reading knowledge base — books, articles, highlights, and evolving notes organised by theme and author.",
        "purpose_md": (
            "# Vault Purpose — Reading\n\n"
            "## Goal\n\n"
            "Capture, organise, and connect insights from books and articles into a personal knowledge library.\n\n"
            "## Key Questions\n\n"
            "- What are the central ideas in each work I read?\n"
            "- How do ideas from different authors relate or conflict?\n"
            "- Which books have changed my thinking the most and why?\n"
            "- What themes or patterns emerge across my reading list?\n\n"
            "## Scope\n\n"
            "**In scope:** Books, long-form articles, essays, podcasts I transcribe.\n"
            "**Out of scope:** News items, social media threads, ephemeral content.\n\n"
            "## Thesis\n\n"
            "Reading without synthesis is entertainment. This vault turns reading into knowledge.\n"
        ),
        "schema_md": (
            "# Synapse Vault Schema — Reading\n\n"
            "Required frontmatter fields for every wiki page:\n\n"
            "| Field | Type | Required | Notes |\n"
            "|-------|------|----------|-------|\n"
            "| `type` | string | yes | entity (author/book), concept, source, synthesis, comparison |\n"
            "| `title` | string | yes | Book title, author name, or concept label |\n"
            "| `sources` | list[string] | no | File paths or ISBN/URL |\n"
            "| `tags` | list[string] | no | Genre, theme, author-name tags |\n\n"
            "Wikilink style: `[[PageTitle]]` (Obsidian-compatible, I5).\n\n"
            "Preferred tags: `#fiction`, `#non-fiction`, `#highlight`, `#insight`, `#author`.\n"
        ),
    },
    {
        "id": "personal-growth",
        "name": "Personal Growth",
        "description": "A self-development knowledge base — goals, habits, reflections, and skills linked to sources and evidence.",
        "purpose_md": (
            "# Vault Purpose — Personal Growth\n\n"
            "## Goal\n\n"
            "Track goals, habits, skills, and reflections in a structured, evidence-based personal development system.\n\n"
            "## Key Questions\n\n"
            "- What are my most important long-term goals and what is my progress?\n"
            "- Which habits have the highest impact on my wellbeing and performance?\n"
            "- What have I learned from past experiments and setbacks?\n"
            "- How do my values and beliefs evolve over time?\n\n"
            "## Scope\n\n"
            "**In scope:** Personal reflections, book notes, experiments, habit tracking, skill maps.\n"
            "**Out of scope:** Work-only professional knowledge (use a separate vault).\n\n"
            "## Thesis\n\n"
            "Deliberate self-knowledge, systematically maintained, compounds over time.\n"
        ),
        "schema_md": (
            "# Synapse Vault Schema — Personal Growth\n\n"
            "Required frontmatter fields for every wiki page:\n\n"
            "| Field | Type | Required | Notes |\n"
            "|-------|------|----------|-------|\n"
            "| `type` | string | yes | entity (habit/skill/person), concept, source, synthesis |\n"
            "| `title` | string | yes | Habit name, skill, goal label, or person |\n"
            "| `sources` | list[string] | no | Book/article that informed this page |\n"
            "| `tags` | list[string] | no | Domain tags like `#habit`, `#goal`, `#skill`, `#reflection` |\n\n"
            "Wikilink style: `[[PageTitle]]` (Obsidian-compatible, I5).\n\n"
            "Preferred tags: `#habit`, `#goal`, `#mindset`, `#skill`, `#experiment`, `#reflection`.\n"
        ),
    },
    {
        "id": "business",
        "name": "Business",
        "description": "A business intelligence knowledge base — market research, competitor analysis, product concepts, and strategic decisions.",
        "purpose_md": (
            "# Vault Purpose — Business\n\n"
            "## Goal\n\n"
            "Build a structured knowledge base to support strategic business decisions, market understanding, and product development.\n\n"
            "## Key Questions\n\n"
            "- Who are the key players in our market and what are their strengths?\n"
            "- What problems do our target customers have and how do we solve them?\n"
            "- What strategic options do we have and what are the trade-offs?\n"
            "- What assumptions are we making and how do we validate them?\n\n"
            "## Scope\n\n"
            "**In scope:** Market research, competitor intel, product specs, strategic memos, customer feedback.\n"
            "**Out of scope:** Internal HR, financial statements (unless used for market analysis).\n\n"
            "## Thesis\n\n"
            "Good decisions come from well-organised, accessible, shared knowledge.\n"
        ),
        "schema_md": (
            "# Synapse Vault Schema — Business\n\n"
            "Required frontmatter fields for every wiki page:\n\n"
            "| Field | Type | Required | Notes |\n"
            "|-------|------|----------|-------|\n"
            "| `type` | string | yes | entity (company/product/person), concept, source, synthesis, comparison |\n"
            "| `title` | string | yes | Company name, product, concept, or market segment |\n"
            "| `sources` | list[string] | no | Report, article, or interview file paths/URLs |\n"
            "| `tags` | list[string] | no | Market, function, or strategic topic tags |\n\n"
            "Wikilink style: `[[PageTitle]]` (Obsidian-compatible, I5).\n\n"
            "Preferred tags: `#competitor`, `#market`, `#product`, `#strategy`, `#customer`, `#risk`.\n"
        ),
    },
    {
        "id": "general",
        "name": "General",
        "description": "A general-purpose knowledge base with no fixed domain — suitable for mixed personal, professional, and learning notes.",
        "purpose_md": (
            "# Vault Purpose — General\n\n"
            "## Goal\n\n"
            "Capture and organise knowledge from any domain into a personal wiki.\n\n"
            "## Key Questions\n\n"
            "- What do I know and what do I still need to learn?\n"
            "- How do ideas from different domains connect?\n"
            "- What are the most important concepts I keep returning to?\n\n"
            "## Scope\n\n"
            "**In scope:** Any document, note, or idea worth retaining.\n"
            "**Out of scope:** Temporary to-do items (use a task manager).\n\n"
            "## Thesis\n\n"
            "<!-- Define your working thesis or vault purpose here. -->\n"
        ),
        "schema_md": (
            "# Synapse Vault Schema — General\n\n"
            "Required frontmatter fields for every wiki page:\n\n"
            "| Field | Type | Required | Notes |\n"
            "|-------|------|----------|-------|\n"
            "| `type` | string | yes | entity, concept, source, query, synthesis, comparison |\n"
            "| `title` | string | yes | Human-readable page title |\n"
            "| `sources` | list[string] | no | Source file paths or URLs |\n"
            "| `tags` | list[string] | no | 3-6 concise, lowercase, reusable navigation tags |\n\n"
            "Wikilink style: `[[PageTitle]]` (Obsidian-compatible, I5).\n\n"
            "YAML frontmatter block must be delimited by `---` at lines 1 and N.\n"
        ),
    },
]

SCENARIO_INDEX: dict[str, dict[str, str]] = {s["id"]: s for s in SCENARIOS}
