# ADR-0035 — Page content read/edit endpoints (`GET`/`PUT /pages/{id}/content`)

- **Status:** Accepted
- **Date:** 2026-06-30
- **Sprint:** v0.5 (M5 hardening — Wiki Notes editing surface)
- **Features:** F1 (3-panel shell — center editor) · F16 (`dataVersion`-driven refresh) · K7 (Obsidian-valid `wiki/`)
- **Resolves:** the `GET /pages/{id}/content` reservation noted in **ADR-0017 §center/right panels**
  ("no content API exists → option (a); `GET /pages/{id}/content` reserved for fast-follow"). This ADR
  promotes that stub to a read **and** write contract.
- **Invariants owned:** **I1** (HEADLINE — a PUT is a *single-page targeted incremental update* through
  the existing write primitives; NEVER a vault rescan; `data_version` +1 exactly once) · **I5**
  (HEADLINE — editing raw markdown keeps `wiki/` a valid Obsidian vault: frontmatter and `[[wikilinks]]`
  are preserved/re-parsed, never corrupted) · I2 (the single `data_version` bump fires the debounced
  GraphCache recompute; no inline FA2) · I7 (single pass, no loop, no inference; a byte cap bounds the
  write)
- **Author:** solution-architect

---

## 1. Context

The Wiki Notes UI (the CodeMirror 6 center editor replacing the redundant center GraphPanel in the
`pages` section — see §8 invariant note in the companion review) needs to **read** a wiki page's raw
markdown and **save** edits back. Today the right-panel inspector renders metadata only (ADR-0017
option (a)); there is no endpoint that returns page **content** and none that writes an edit.

Two facts about the existing system constrain the design and are easy to get wrong:

1. **`pages` rows are metadata-only.** The `Page` ORM row stores `file_path`, `title`, `type`,
   `sources[]` (JSONB), `content_hash`, coords, `deleted_at` — **not** the markdown body. The body
   lives **on the filesystem** at `vault/<file_path>` (`wiki/<subdir>/<slug>.md`). Postgres is the
   metadata/links system of record (ADR-0002); the file is the content system of record. The content
   API must therefore read/write the **file**, and keep the metadata row consistent with it.

2. **The watcher does NOT observe `vault/wiki/`.** `watcher.py` schedules its observer on
   `vault/raw/sources/` only (`observer.schedule(handler, str(watch_dir))`, `watch_dir = raw/sources`).
   A write to a `wiki/*.md` file produces **no** watcher event. This is by design (ADR-0026 §5
   "Watcher non-interference": backend writes to `wiki/` are invisible to the watcher, which prevents
   re-ingest loops). **Consequence:** the PUT endpoint cannot "let the watcher re-index it" — there is
   no watcher on `wiki/`. It MUST re-index the edited page **inline**, through the same incremental
   primitives the orchestrated writer uses, touching only that one page (I1). This is *more* incremental
   than a watcher event, not less: zero file-system round-trip, one page updated, one `data_version`
   bump.

### 1.1 Ground truth consumed (reuse, do not reinvent — I9)

- **`Page` model + `uix_pages_vault_file_path_live`** (`models.py`) — the live page row keyed by
  `(vault_id, file_path)` `WHERE deleted_at IS NULL`. `content_hash` = `sha256` of the serialized file
  (`write_wiki_page` sets `content_hash=_sha256(serialized.encode())`). This hash is the **optimistic
  lock token**.
- **`write_wiki_page` → persist_metadata → upsert_vector → append_log → bump_version → parse/persist
  wikilinks → update_index** (`ingest/orchestrator.py`) — the SINGLE incremental wiki-write seam (I1).
  The PUT endpoint reuses **the same primitives** to re-index the edited content (it does **not** call
  `write_wiki_page` verbatim, because that path rebuilds frontmatter from a typed `WikiFrontmatter` and
  re-slugs the title; the editor saves a **raw file** whose frontmatter the user may have touched — so
  PUT writes the user's bytes and then re-runs the *indexing* primitives: `persist_metadata` for the
  metadata fields, `upsert_vector` for the body, `persist_links` for wikilinks, `update_index`,
  `bump_version`). Same primitives, raw-file write.
- **`parse_wikilinks` / `persist_links`** (`wiki/links.py`) — K5 incremental link upsert. Re-run on the
  saved body so `links` reflects the user's edits (added/removed `[[wikilinks]]`).
- **`upsert_vector`** (`ingest/orchestrator.py`) — re-embeds the body into Qdrant under the page UUID
  (no-op when `EMBEDDINGS_ENABLED=false`, ADR-0030). Same point id, so search stays consistent (I1).
- **`bump_version()` + GraphCache debounce** (`graph/cache.py`, ADR-0014) — one bump → the debounced
  FA2 recompute fires on its own schedule (I2; never inline).
- **`_relative_path` / path containment** (the basename/`..` sanitization pattern in `upload.py`,
  ADR-0020) — the path-traversal guard model PUT/GET reuse.
- **`@app.delete("/provider/config/{id}")` / `DELETE /pages/{id}`** (`main.py`, ADR-0026) — the house
  path-param + 404 pattern the new routes mirror.

---

## 2. Decision summary

Add two routes under the existing `/pages/{id}` namespace:

```
GET /pages/{id}/content   → 200 {file_path, title, type, content_hash, content}
PUT /pages/{id}/content   → 200 {file_path, content_hash, data_version}   (new hash + new version)
```

- **`pages` rows stay metadata-only.** Content is read from / written to the **file** at
  `vault/<file_path>`. The endpoints never add a content column to `pages` (ADR-0002 split preserved).
- **GET** resolves the page row (404 if absent or soft-deleted), reads the file bytes, and returns the
  raw markdown **including** the YAML frontmatter block plus the row's `content_hash` as the lock token.
- **PUT** takes `{content, content_hash}`:
  1. Resolve the live page row by id (404 if absent/soft-deleted).
  2. **Optimistic-lock check:** if the supplied `content_hash != row.content_hash` → **409 Conflict**
     (the file changed under the editor; the client must re-GET and merge). No write happens.
  3. **Path-traversal guard:** recompute the absolute path as
     `(vault_root / row.file_path).resolve()`, assert it is inside `vault_root.resolve()` and that
     `row.file_path` starts with `wiki/` (content edits are wiki-only; `raw/sources/` is immutable, K1).
     A row whose `file_path` escapes the vault or targets `raw/` → **403**. The client never supplies a
     path — only the page **id** — so traversal is structurally hard; this guard is defence-in-depth.
  4. **Bound (I7):** reject a body larger than `MAX_PAGE_CONTENT_BYTES` (default 5 MB) → **413**.
  5. **Atomic write:** write the new bytes to a sibling temp file in the same directory and
     `os.replace()` it onto `row.file_path` (atomic same-filesystem rename — no torn file if the process
     dies mid-write; readers never see a half-written page).
  6. **Inline incremental re-index (I1 — the critical step):** recompute
     `new_hash = sha256(new_bytes)`, then run the existing index primitives for **this one page only**:
     `persist_metadata` (update `content_hash`, and `title`/`type`/`sources[]` parsed from the saved
     frontmatter so metadata tracks the edit) → `upsert_vector` (re-embed the body under the same UUID)
     → `parse_wikilinks` + `persist_links` (re-derive this page's `links` rows) → `update_index` →
     `bump_version()` **exactly once**. No other page is read or written; no vault walk.
  7. Return `{file_path, content_hash: new_hash, data_version: <new>}`.

**No inference, no loop, no FA2 in this path.** It resolves no provider (I6 untouched — no provider is
involved), runs no bounded loop, and never calls `GraphEngine.recompute()` (I2 — the debounced cache
fires on the bump).

### 2.1 Request/response contract (D4)

```
GET /pages/{id}/content
  200 → { "file_path": "wiki/concepts/x.md", "title": "X", "type": "concept",
          "content_hash": "<sha256>", "content": "---\ntype: concept\n...\n---\n\nBody [[Y]]…" }
  404 → page id unknown OR soft-deleted

PUT /pages/{id}/content
  body: { "content": "<full raw markdown incl. frontmatter>", "content_hash": "<sha256 from GET>" }
  200 → { "file_path": "...", "content_hash": "<new sha256>", "data_version": 124 }
  404 → page id unknown OR soft-deleted
  409 → content_hash stale (file changed since GET) — re-GET and retry; NO write performed
  413 → body exceeds MAX_PAGE_CONTENT_BYTES
  403 → resolved path escapes vault_root OR targets a non-`wiki/` file (raw/ is immutable)
```

`make openapi` regenerates `docs/api/openapi.json` with both routes (I8). No DB migration — `pages`
gains no column; `content_hash` already exists. D2 unchanged.

---

## 3. Optimistic concurrency (the 409 contract)

`content_hash` (`sha256` of the on-disk serialized file) is the version token. The flow is read-modify-
write with compare-and-swap:

1. The editor **GET**s `{content, content_hash=H0}` and lets the user edit in CodeMirror.
2. On save it **PUT**s `{content=edited, content_hash=H0}`.
3. The server compares `H0` against the **current** `row.content_hash`:
   - **match** → write, set the row's hash to `sha256(new_bytes)`, return the new hash. The lock is
     advanced; the next save must carry the new hash.
   - **mismatch** → **409**. Something changed the page since the GET (a cascade-delete rewrite,
     ADR-0026; a Create from the review queue, ADR-0034; a re-ingest; another editor tab). The client
     re-GETs, shows the user the divergence, and retries. **No partial/last-writer-wins clobber.**

The hash is compared against `row.content_hash` (Postgres), which `persist_metadata` keeps in lock-step
with every write through the seam — so the lock is authoritative even though the body lives on disk.
(Belt-and-braces option, deferred: also hash the on-disk bytes at PUT time and 409 if the file drifted
from the row; not needed in M5 because every legitimate `wiki/` writer goes through the seam and updates
the row hash. Recorded as §6 risk 2.)

---

## 4. Why inline re-index, not a watcher event (I1 clarification)

The naïve framing "PUT writes the file, the watcher debounces one FS event and re-indexes" does **not**
apply here: the watcher observes `raw/sources/` only and is deliberately blind to `wiki/` (ADR-0026 §5).
Extending the watcher to `wiki/` would be **wrong** — it would re-fire on Synapse's own wiki writes
(cascade-delete rewrites, review Creates, orchestrated generation), creating exactly the re-ingest loop
ADR-0026 §5 prevents. So PUT re-indexes **inline**:

- It updates **one** page's metadata row, **one** Qdrant point, **one** page's `links` rows, the
  catalogue, and bumps `data_version` once. This is the textbook I1 "a file change updates only affected
  records" — strictly a single-page targeted update.
- It NEVER enumerates `vault/wiki/`, NEVER re-embeds other pages, NEVER rebuilds the graph inline.
- The single `bump_version()` triggers the debounced FA2 recompute (I2), so the edited page's new/removed
  links eventually re-flow the layout server-side — never on the UI thread.

This is more incremental than a watcher round-trip (no FS event, no re-read of the file we just wrote).

---

## 5. Obsidian compatibility (I5)

The editor saves **raw markdown including the frontmatter block** — the user edits the same text Obsidian
shows. The endpoint does **not** round-trip the frontmatter through PyYAML on the **write** path (which
would reorder keys / reflow lists — the DEFECT-F13-002 class of corruption, ADR-0026 §4.3): PUT writes
the user's bytes verbatim. To keep the metadata row consistent it **parses** the saved frontmatter
read-only (`frontmatter.loads`) to extract `title`/`type`/`sources[]` for `persist_metadata`; the parse
is non-mutating and the bytes on disk are exactly what the user typed.

- If the user's edit produces **invalid YAML frontmatter**, the parse fails. PUT then returns **422**
  with a clear message and **does not write** (an invalid-frontmatter page would break Obsidian and the
  indexer — fail closed, never persist a vault-invalidating file). The body-only edit case (no
  frontmatter change) parses fine and proceeds.
- `[[wikilinks]]` the user adds/removes are re-parsed by `parse_wikilinks` and re-persisted, so the K5
  link graph and the dangling flags stay correct — the vault's link structure tracks the edit.
- The file stays a valid Obsidian note: frontmatter fence intact, wikilinks intact, body markdown intact.
  `test_obsidian_check.py` remains the gate (must stay green after an edit round-trip).

---

## 6. Flagged tensions & risks

1. **Title/slug edits via frontmatter.** If the user edits the `title` in frontmatter, the **file name**
   (`<slug>.md`) no longer matches the new title. M5 decision: PUT updates the **metadata** `title` but
   does **not** rename the file (a rename is a delete+create that touches every inbound `[[wikilink]]` —
   that is cascade-delete/rename territory, ADR-0026, out of scope here). The slug-title drift is
   cosmetic and Obsidian-valid (the filename is just a slug). A proper title-rename flow is a post-M5
   ADR. Recorded, not built.
2. **Out-of-band file drift.** The 409 lock compares the request hash to `row.content_hash`, which every
   in-seam writer updates. A hand-edit on the TrueNAS filesystem (outside Synapse) would drift the file
   from the row without bumping the row hash; PUT would then overwrite it. Acceptable for M5 (single
   operator; Synapse owns `wiki/`). The optional on-disk re-hash guard (§3) closes this if it ever bites.
3. **Concurrent PUT + cascade-delete/Create race.** Both advance `content_hash` through the seam, so the
   second writer's 409 fires correctly (compare-and-swap). The atomic `os.replace` ensures no torn file.
4. **No content column.** Reading from disk on every GET is an extra FS read vs. a DB column, but it
   preserves the ADR-0002 metadata/content split and avoids a dual-write consistency problem (the file is
   the content source of truth). Acceptable; the file read is cheap and bounded by the 5 MB cap.

---

## 7. Do-NOT list (rejection triggers — any one is a block at PR review)

1. **DO NOT** add a `content` column to `pages` or otherwise store the body in Postgres — content is on
   the filesystem; `pages` is metadata-only (ADR-0002).
2. **DO NOT** rescan the vault on save. PUT updates exactly ONE page through the index primitives and
   bumps `data_version` ONCE (I1). A `glob('wiki/**/*.md')` or a full re-embed on save is a reject.
3. **DO NOT** subscribe the watcher to `vault/wiki/` to "pick up" the edit — that recreates the
   ADR-0026 §5 re-ingest loop. Re-index **inline** in the endpoint instead.
4. **DO NOT** call `GraphEngine.recompute()` / FA2 inline (I2). One `bump_version()`; the debounced cache
   does the rest.
5. **DO NOT** skip the optimistic-lock check. A stale `content_hash` is a **409**, never a
   last-writer-wins clobber.
6. **DO NOT** accept a client-supplied file path — only the page **id**; resolve the path from the row
   and assert vault containment + `wiki/` prefix (no `raw/`, no `..`, no absolute). Path escape → 403.
7. **DO NOT** PyYAML round-trip the frontmatter on the **write** path (key reorder / list reflow =
   I5 corruption). Write the user's bytes verbatim; parse read-only for metadata.
8. **DO NOT** persist a page whose frontmatter is invalid YAML (would break Obsidian) — 422, no write.
9. **DO NOT** write the file non-atomically. Temp-file + `os.replace()` so a crash never leaves a torn
   page (a half-written page breaks I5 and the indexer).
10. **DO NOT** ship without `make openapi` regenerated (I8). No DB migration is needed (no D2 change).

---

## 8. Invariant compliance

| Inv | How this design guarantees it |
|-----|-------------------------------|
| **I1** | A PUT is a single-page targeted incremental update: one metadata row, one Qdrant point, one page's `links`, one `update_index`, one `data_version` +1. No vault walk, no re-embed of other pages, no watcher round-trip. This is the canonical I1 "change updates only affected records". |
| **I2** | The single `bump_version()` triggers the debounced GraphCache recompute; the endpoint never runs FA2 inline. Layout stays server-side and cached. |
| **I5** | The editor saves raw markdown incl. frontmatter; PUT writes the bytes verbatim (no PyYAML reorder), re-parses `[[wikilinks]]` (K5), and refuses to persist invalid-YAML frontmatter (422). `wiki/` stays a valid Obsidian vault; `test_obsidian_check.py` is the gate. |
| **I7** | No loop, no inference. The write is bounded by `MAX_PAGE_CONTENT_BYTES` (413). Single pass. |
| **I6** | Not engaged — the content path involves no inference provider; nothing is hardcoded because nothing is routed. |
| **I8** | `make openapi` regenerates `docs/api/openapi.json` (two new routes). No schema change → D2 unchanged. D3: the existing wiki-write sequence may note the editor PUT as an alternate entry; no new container (D1 unchanged — the route lives in the existing FastAPI service). |

No invariant is traded. The two genuine tensions (title-rename, out-of-band drift) are flagged in §6 and
deferred with recorded rationale.

---

## 9. Implementation plan

- **backend-engineer [BE]:** add `GET`/`PUT /pages/{id}/content` to `main.py` (Pydantic
  `PageContentResponse` / `PageContentUpdate`); the GET file read; the PUT lock check (409), path guard
  (403), byte cap (413), invalid-frontmatter guard (422), atomic temp-file `os.replace` write, and the
  inline re-index via `persist_metadata`/`upsert_vector`/`parse_wikilinks`+`persist_links`/`update_index`/
  `bump_version`. Add `MAX_PAGE_CONTENT_BYTES` to `config.py`. Run `make openapi` → commit
  `docs/api/openapi.json` (I8). Tests: GET 200/404; PUT 200 bumps `data_version` exactly once + advances
  hash + re-persists wikilinks; 409 on stale hash performs no write; 413 on oversize; 403 on a non-`wiki/`
  or escaping path; 422 on invalid frontmatter; `test_obsidian_check.py` green after an edit round-trip;
  PUT does NOT touch any other page's row/point.
- **frontend-engineer [FE]:** the Wiki Notes CodeMirror 6 editor (see the companion invariant review)
  fetches `GET /pages/{id}/content`, edits, and `PUT`s `{content, content_hash}`; on **409** it re-GETs
  and surfaces a conflict notice; on success it relies on the bumped `data_version` (F16) to refresh the
  tree/graph. The editor is CodeMirror 6 (I4 — never WYSIWYG/ProseMirror).
- **tech-writer:** this ADR (format + README row), USER.md (editing a wiki page, conflict behavior), and
  the D4 OpenAPI surface. No D1 topology change (no new container/component).

---

## 10. Sign-off

**APPROVED to implement.** `pages` stays metadata-only; content read/write targets the filesystem.
GET returns raw markdown + the `content_hash` lock token; PUT does compare-and-swap (409 on stale),
guards path traversal (403) and size (413), refuses invalid-frontmatter (422), writes atomically, and
re-indexes the single edited page **inline** through the existing incremental primitives — one
`data_version` bump, no rescan, no watcher event, no FA2. I1 and I5 are satisfied by construction;
I2/I7 are honoured; I6 is not engaged. No DB migration; `make openapi` regenerated (I8).

> Handoff: ADR-0035 → tech-writer (format, README row, USER.md). Interface contract (§2) →
> backend-engineer [BE] + frontend-engineer [FE]. PR verdicts → orchestrator.
