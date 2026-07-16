# ADR-0077 — Schema-driven page-type routing and an open page-type set (v1.7.0)

- **Status:** Accepted
- **Date:** 2026-07-14
- **Depends on:** ADR-0076 (block-based ingest pipeline)
- **Invariants touched:** I5, I6, I8
- **Reference:** `docs/reference/LLMWIKI-CORE-LOGIC-v0.6.3.md` §5.1, §1.3

## Context

Synapse fixed the six user-content page types in a `PageType` StrEnum with a hardcoded
`type → subdir` map (`ingest/schemas.py`). nashsu/llm_wiki instead treats the vault's `schema.md`
"Page Types" table as the **authoritative** routing rule: a page's frontmatter `type` maps to a
directory via that table, custom types included (goal, habit, character, thesis, methodology,
finding, …), and a page whose `type` doesn't match the directory in its FILE-block path is dropped.
The 1.7.0 scenario templates (ADR-0081) rely on this — the Research template adds `thesis`,
`methodology`, `finding`; Reading adds `character`, `theme`, `plot-thread`, `chapter`; etc. With a
closed six-type enum those pages are impossible.

## Decision

### 1. A provider-neutral schema-routing module

`app/wiki/schema.py` (pure, stdlib-only) ports `wiki-schema.ts`:
- `parse_page_type_routing(schema_md) -> {type: subdir}` parses the `## Page Types` markdown table
  (dir cell must be `wiki` or start `wiki/`; malformed/header/separator rows dropped; table read
  only within the Page-Types section; later rows win). It returns exactly what the table declares
  — base types are **not** auto-injected.
- `validate_page_routing(page_type, rel_path, routing) -> (ok, error)` reproduces
  `validateWikiPageRouting`'s two error conditions (declared type routed to a dir the path doesn't
  use; path's dir routed to a different type than declared).
- `subdir_for_type(page_type, routing)` resolves a (possibly custom) type: routing first, then the
  base defaults `BASE_TYPE_DIRS`, then the type name itself as a last-resort custom dir.

Routing values are Synapse's **bare** subdirs (`entities`, `thesis`), consistent with
`ingest/schemas.py::_TYPE_DIR` and the vault bootstrap dirs; error messages render back to the
`wiki/…` form so the ported TS assertions hold verbatim.

### 2. The open type set lives in the block path, not the enum

The strict `PageType` enum and `WikiPage`/`WikiFrontmatter` models are **unchanged** — they remain
the JSON rollback path's contract (ADR-0076). Custom-typed pages exist only on the block-based
path, which persists `pages.page_type` as the **raw string** (the column is already nullable text,
no migration). The block writer places a page at its FILE-block path, validates it against the
parsed routing, and drops a mis-routed or app-managed (index/overview) block — matching llm_wiki.

### 3. The generation prompt embeds the table as authoritative

The block generation prompt (ADR-0076, `ingest/prompts.py`) includes the vault's `schema.md` under
"Project Schema and Routing (AUTHORITATIVE)" and instructs the model that every page's frontmatter
`type` must match the schema directory in its FILE path — so routing is steered at generation and
enforced at write.

## Consequences

- Template parity (ADR-0081) is possible: custom types route and persist correctly (verified
  end-to-end — the routing parser reads every 1.7.0 template table and its dirs match the
  scaffolded `extra_dirs`).
- The JSON path and the six-type enum are untouched, so the change is additive and low-risk (I6:
  no provider branching; routing is data, not code).
- I5 preserved: types are ordinary frontmatter strings; `wiki/` stays a valid Obsidian vault.
- I8: this ADR + `test_wiki_schema_routing.py` (31 cases) satisfy the docs/test gate.
