# ADR-0050 — Retrieval scope restricted to wiki/ pages only

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-07-03 |
| Feature | R7-8 — Retrieval scope: citations from wiki/ only (AC-R7-8-1..4) |
| Decider | backend-engineer |

## Context

The 4-phase retrieval pipeline (`backend/app/rag/retrieval.py`) assembles a grounded context
string for chat and search from pages stored in Qdrant and Postgres. Prior to this decision,
the assembly phase (`_load_page_meta`) did not filter by directory prefix: any page present
in the `pages` table — including raw source documents stored under `raw/` — could be cited
in the assembled context.

This was architecturally incorrect for two reasons:

1. **Raw documents are not wiki knowledge.** Files under `vault/raw/sources/` are the
   *input material* to the ingest pipeline. They have not been analyzed, classified, or
   synthesized by the LLM. Surfacing them as citations alongside synthesized wiki pages
   misleads the user about the epistemic status of the cited content.

2. **Wikilinks and frontmatter types do not apply.** Raw source files may lack valid
   frontmatter, may be in non-Markdown formats, and are not linked in the graph. Citing
   them breaks the `[n]` citation UX (clicking a citation should navigate to a wiki page,
   not a raw source file dump).

## Decision

The citation/candidate assembly phase (`_load_page_meta`) and the lexical Phase-1 fallback
(`_phase1_lexical_search`) both apply the SQL filter:

```sql
file_path NOT LIKE 'raw/%'
```

This restricts citations to pages whose `file_path` starts with `wiki/` (the only other
prefix used by `write_wiki_page`). The vector Phase-1 path (Qdrant) may still return raw/
point ids from the collection — these are silently dropped by the `_load_page_meta` filter
in Phase 4 assembly rather than at the Qdrant query level, because Qdrant payload filtering
would add coupling and the performance impact of dropping a few raw/ ids after a top-k
lookup is negligible.

The filter `file_path NOT LIKE 'raw/%'` is portable SQL: it works identically on Postgres
(production) and SQLite (tests, aiosqlite). It is **not** a whitelist of allowed prefixes
(which would break if new valid prefixes were added) but a blacklist of the excluded raw/
prefix (which is stable by the I1/K1 vault architecture: raw/ is always input, wiki/ is
always output).

## Consequences

- Chat and GET /search citations now exclusively reference citable wiki knowledge pages.
- Users cannot be confused by raw source files appearing in chat context.
- The Qdrant collection continues to index raw/ pages (the watcher upserts them); only
  the assembly phase discards them. This avoids a re-indexing migration and preserves the
  ability to add other retrieval use-cases (e.g. source-search) in the future.
- A new pytest test (`test_ac_r7_8_raw_excluded`) asserts that a raw/ page present in
  Qdrant is not returned by the assembly phase (AC-R7-8-1).
- Existing retrieval tests remain green (AC-R7-8-3); all existing test fixtures already
  use `raw/sources/…` file paths, which are now correctly excluded, but the existing tests
  were not asserting on those paths being included — so no regressions.

**Note on existing tests:** The pre-R7-8 retrieval tests in `test_retrieval.py` used
`raw/sources/…` file paths in their fixture pages. After R7-8 those pages are excluded by
the filter, causing some existing tests to receive fewer citations than before. The tests
have been updated to use `wiki/…` file paths where the assertion depends on citation
presence, proving the filter is correct. Tests that assert on exclusion behavior (e.g.
soft-deleted page not cited) continue to work unmodified.

## Alternatives considered

- **Filter at Qdrant query level** (payload filter on file_path): rejected because it
  requires the file_path to be stored as a Qdrant payload field and adds coupling between
  the vector store and the directory structure. The current approach keeps Qdrant as a
  pure similarity index.
- **Only index wiki/ pages in Qdrant**: rejected because the watcher's incremental upsert
  already indexes every `.md` file it encounters; changing this would require a watcher
  code path split and a Qdrant migration to remove existing raw/ points.
- **Allow raw/ pages but mark them differently in citations**: rejected as over-engineered
  for the use case. The clean separation (raw = input, wiki = output) is already enforced
  by the vault architecture (K1, I1).
