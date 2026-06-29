# Synapse Vault Schema

Required frontmatter fields for every wiki page:

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `type` | string | yes | entity, concept, source, query, synthesis, comparison |
| `title` | string | yes | Human-readable page title |
| `sources` | list[string] | no | Source file paths or URLs |

Wikilink style: `[[PageTitle]]` (Obsidian-compatible, I5).

YAML frontmatter block must be delimited by `---` at lines 1 and N.
