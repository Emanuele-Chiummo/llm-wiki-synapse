# ADR-0026 — Cascade deletion of wiki pages (F13)

- **Status:** Accepted
- **Date:** 2026-06-29
- **Sprint:** v0.5 (M5 Phase 4)
- **Feature:** F13 (Cascade deletion — 3-method matching, preserve shared entities, cleanup index.md + dead wikilinks)
- **Supersedes the F13 interface stub in:** `docs/sprints/v0.5-architecture.md` §2.5 / §6.5 (this ADR is the detailed design; the stub stands as the coherence map)
- **Resolves:** AQ-v0.5-5 (soft delete `deleted_at=now()`; the watcher already ignores soft-deleted rows; F13 also deletes the originating `raw/sources/` file so there is no resurrection on restart)
- **Invariants owned:** **I1** (HEADLINE — targeted edits only; NEVER a full vault rescan; `data_version` +1 exactly once; NO synchronous FA2) · **I5** (HEADLINE — dead-wikilink cleanup is frontmatter-safe; `wiki/` stays a valid Obsidian vault) · I2 (the single `data_version` bump fires the debounced GraphCache recompute, no forced inline FA2) · I7 (single pass, no loop; one bounded provider-free operation)
- **Author:** solution-architect

---

## 1. Context

F13 is the highest vault-integrity risk in M5. Deleting a wiki page is a **destructive, multi-store**
operation that must keep four artifacts consistent without ever scanning the vault:

1. **Postgres** — the deleted page's `pages` row (soft-delete), the `links` rows that point at it,
   and the `edges` rows that touch it.
2. **Qdrant** — the deleted page's vector point.
3. **`vault/wiki/index.md`** — the K3 catalogue entry for the deleted page.
4. **Every other `wiki/*.md` page that contains `[[<deleted title>]]`** — those wikilinks become
   **dead** the instant the target is gone, and a dead `[[wikilink]]` is exactly the I5 failure class
   (Obsidian shows it as a broken link; `test_obsidian_check.py` is the gate).

Two invariants dominate the design and cannot be traded:

- **I1 — incremental only.** F13 must touch ONLY the files that reference the deleted page. It must
  find those files from the **`links` table back-reference index** (built incrementally by
  `persist_links` on every `write_wiki_page`), NEVER by walking `vault/wiki/`. `data_version` is bumped
  **exactly once**; no FA2 is forced (the existing debounced `GraphCache` recompute fires on the bump).
- **I5 — Obsidian-valid.** Rewriting a dead `[[Target]]` to plain `Target` must operate on the **body
  only** and must never corrupt the YAML frontmatter block. The rewrite is a body-scoped, anchored
  string replacement, not a blind global replace.

### 1.1 Ground truth consumed (existing seams — reuse, do not reinvent)

- **`pages` model (`models.py`).** Soft-delete is `deleted_at` (ADR-0005); the partial-unique index
  `uix_pages_vault_file_path_live` is `WHERE deleted_at IS NULL`, so a tombstoned row never blocks a
  later re-create. `pages.file_path` for a **wiki page** is `wiki/<subdir>/<slug>.md`; for a **source**
  it is `raw/sources/...`. `pages.sources` is the K6 JSONB `sources[]` array (frontmatter provenance).
- **`links` model.** One row per `[[Target]]` occurrence: `source_page_id` = the page (file) that
  *contains* the wikilink; `target_title` = the literal `[[Target]]` string as written;
  `target_page_id` = the resolved FK (NULL while dangling); `dangling` flag. **This table IS the
  no-rescan back-reference index** — given a deleted page, the rows with `target_page_id == deleted`
  (or `target_title` matching the deleted title) are exactly the referencing files (§4.2).
- **`edges` model.** 4-signal weighted undirected pairs (ADR-0012/0016); `kind ∈ {link, source}`;
  indexed on both endpoints (`ix_edges_source_page_id` / `ix_edges_target_page_id`, comment already
  cites "cascade cleanup (F13, v0.5)"). The **shared-entity** check reads `edges` (source-overlap), it
  never recomputes them inline.
- **`delete_file()` / `delete_point()` (`orchestrator.py` / `qdrant_client.py`).** The existing
  soft-delete path: `pages.deleted_at = now()` + Qdrant point hard-delete. F13 **mirrors** this
  primitive (it does not reinvent soft-delete) and extends it with the cleanup steps.
- **`update_index()` (`wiki/index.py`).** Regenerates `index.md` from **live** pages
  (`deleted_at IS NULL`). Once the deleted page is tombstoned, calling `update_index` once **removes
  its catalogue entry automatically** — F13 does not hand-edit `index.md`.
- **`parse_wikilinks()` / `persist_links()` (`wiki/links.py`).** The wikilink grammar
  (`[[Target]]` / `[[Target|alias]]` / `[[Target#section]]`) and the incremental link upsert. F13
  reuses `persist_links` to re-derive the `links` rows of each rewritten file (so the back-reference
  index stays correct after the rewrite).
- **`bump_version()` + `_graph_cache.notify_bump()` (`orchestrator.py`).** The single `data_version`
  +1 + the GraphCache debounce notification (I2). F13 calls this **once** at the end.
- **`@app.delete("/provider/config/{config_id}")` (`main.py`).** The house DELETE pattern (path-param
  UUID, 404 on miss, typed response). F13's REST mirrors it.

---

## 2. Decision summary

`backend/app/ops/cascade_delete.py` implements a **single-pass, inference-free** operation
(NOT a loop). The entry point computes a plan from Postgres + the `links` back-reference index,
applies it with targeted file writes, and bumps `data_version` exactly once. A **mandatory dry-run /
preview** path computes the same plan **without applying it** (this deletes user data — preview is not
optional). Capability-aware routing, providers, and FA2 are **not involved** — F13 makes **zero**
inference calls and **zero** FA2 calls.

```python
# backend/app/ops/cascade_delete.py  (signatures only — no implementation in this ADR)

@dataclass(frozen=True)
class WikilinkRewrite:
    """One dead [[Target]] → plain-text rewrite in one referencing wiki file."""
    source_page_id: uuid.UUID      # the wiki page (file) that contains the dead link
    file_path: str                 # wiki/<subdir>/<slug>.md  (targeted write target)
    target_title: str              # the [[Target]] string being neutralised
    occurrences: int               # how many [[Target]] spans in this file's BODY

@dataclass(frozen=True)
class CascadePlan:
    """The computed effect of deleting `page_id` — returned by preview, consumed by apply."""
    target_page_id: uuid.UUID
    target_title: str | None
    target_file_path: str                          # wiki/... of the page being deleted
    will_delete: list[uuid.UUID]                    # pages whose sources[] becomes empty (incl. target)
    will_preserve_with_pruned_source: list[uuid.UUID]  # shared pages: keep, prune one sources[] entry
    wikilinks_to_rewrite: list[WikilinkRewrite]     # dead-link cleanup edits (no-rescan, §4)
    index_entry_will_be_removed: bool
    raw_source_to_delete: str | None               # raw/sources/... file removed (AQ-v0.5-5); None if N/A
    shared_entity_warnings: list[str]               # source-overlap pages (edges) — WARN, never block
    match_methods_used: dict[str, str]              # ref_file_path → "exact" | "slug" | "fulltext" (AC-F13-2)

@dataclass(frozen=True)
class CascadeResult:
    """The applied outcome — backs the DELETE response (AC-F13-5)."""
    deleted_page_id: uuid.UUID
    wikilinks_cleaned: int                          # total [[Target]] spans neutralised
    index_entry_removed: bool
    shared_entity_warnings: list[str]
    files_written: int                              # MUST equal len(plan.wikilinks_to_rewrite) (AC-F13-4a)
    data_version_after: int

async def plan_cascade_delete(page_id: uuid.UUID) -> CascadePlan:
    """DRY-RUN. Compute the full effect WITHOUT mutating any store or file. Read-only:
    no soft-delete, no Qdrant delete, no file write, no data_version bump (AC-F13 preview gate)."""

async def cascade_delete(page_id: uuid.UUID) -> CascadeResult:
    """SINGLE PASS (not a loop). Computes the plan via plan_cascade_delete(), then applies it:
    soft-delete → Qdrant delete → targeted wiki rewrites → re-persist their links →
    update_index() once → delete raw/sources/ file → bump_version() ONCE. Idempotent on a
    soft-deleted page: raises PageNotFoundError → HTTP 404 (AC-F13-5c / AC-F13-7c)."""
```

---

## 3. The 3-method reference matching (AC-F13-1, AC-F13-2)

To delete a wiki page Synapse must find **every other wiki file that links to it**. The `links` table
is the primary, no-rescan index, but a wikilink is a free-text title string that may not have resolved
to `target_page_id` (dangling), or may be written with case/spacing variation. So matching is a
**3-method union**, tried in priority order, and the method that found each reference is **logged**
(one log line per found reference with its method label — AC-F13-2).

Let `T` = the deleted page's `title` (and its `slug = _slugify(T)`).

| # | Method | How it finds referencing files | No-rescan? |
|---|--------|--------------------------------|-----------|
| **(a)** | **Exact resolved/title match in `links`** | `SELECT source_page_id, target_title FROM links WHERE target_page_id = :deleted_id OR target_title = :T`. Catches every link already resolved to the page, plus literal-title dangling links. | **Yes** — pure index read. The primary, authoritative method. |
| **(b)** | **Slug-normalised match in `links`** | For the remaining `links` rows not caught by (a), compare `_slugify(target_title) == _slugify(T)`. Covers `[[my page]]` vs title `My Page`, mixed case, and hyphen/space variants. Evaluated over the **`links` rows only** (still no vault walk). | **Yes** — read `links`, normalise in Python. |
| **(c)** | **Full-text scan of `wiki/*.md` — FALLBACK ONLY** | A `[[Target]]` literal-substring scan across `wiki/*.md` bodies, run **only** to catch a link that exists on disk but is **absent from `links`** (e.g. a hand-edited file the indexer has not re-parsed). This IS a file read — see §3.1 for why it is I1-safe and bounded. | Bounded fallback (see §3.1). |

The union of (a) ∪ (b) ∪ (c), de-duplicated by `source_page_id` (or by file_path for (c)-only hits),
is the set of files needing a rewrite. Each entry records its winning method in
`CascadePlan.match_methods_used` for the AC-F13-2 log assertion.

### 3.1 Why the (c) full-text fallback does NOT break I1

I1 forbids a **full-vault rescan as the indexing strategy** — re-deriving the whole index from a vault
walk on every change. The (c) fallback is **not** that: it is a **last-resort consistency net**, scoped
to the **already-enumerated set of live wiki pages** (the `pages` rows with
`file_path LIKE 'wiki/%'` and `deleted_at IS NULL`), reading each candidate file **once** purely to
substring-match `[[T]]`. It does not re-embed, does not re-parse the whole vault, does not rebuild the
graph, and touches no source files. It exists because a hand-edited dead link that the indexer never
saw would otherwise survive the delete and break I5 — the lesser risk is the bounded read.

**Bounding (I7):** (c) is gated by `CASCADE_FULLTEXT_MAX_FILES` (default 5000) and is **skipped
entirely** when (a) ∪ (b) already covers the candidate set with no dangling-title ambiguity, so the
common case never touches the disk for matching. The architect-review gate (§9) requires that the test
suite proves (c) is NOT invoked on the happy path (links-table hit).

---

## 4. Preserve shared entities + the no-rescan dead-link cleanup

### 4.1 The preserve-shared rule (AC-F13-2 preserve clause, AC-F13-3) — EXACT

A wiki page produced from **multiple** source documents (its frontmatter `sources[]` lists more than
one `raw/sources/...` path) must **survive** the deletion of any single one of those sources. The rule
is precise and asymmetric:

> When deleting a **source** document `X` (a `raw/sources/...` page), partition the wiki pages that
> reference `X` in their `sources[]`:
> - **DELETE** a wiki page **iff** removing `X` from its `sources[]` leaves `sources[]` **empty**
>   (`X` was its only provenance). These go in `will_delete`.
> - **PRESERVE + PRUNE** a wiki page whose `sources[]` still contains at least one other entry after
>   removing `X`: the page is **kept**, its frontmatter `sources[]` has the `X` entry removed (a
>   targeted, frontmatter-safe edit — §4.4), and its `pages.sources` JSONB is updated to match. These
>   go in `will_preserve_with_pruned_source`.

When the deletion target is itself a **wiki page** (not a source), `will_delete = {that page}` and the
`sources[]` partition is empty — the rule degenerates cleanly.

**Shared-entity warnings (AC-F13-3) are advisory, never blocking.** Pages connected to the target by a
`kind='source'` edge in the `edges` table (source-overlap) are surfaced as `shared_entity_warnings`
(a WARNING log line + a `shared_entity_warnings: list[str]` field on the response). **The deletion
always proceeds** — shared entities do NOT block it (AC-F13-3a). The warning is for the human to review
after the fact (and is shown in the confirmation modal, AC-F13-6a).

### 4.2 Finding dead wikilinks WITHOUT a vault walk (the I1-critical algorithm)

The set of files whose `[[Target]]` becomes dead is **exactly** the §3 3-method reference set, computed
**primarily from the `links` table** (the incremental back-reference index `persist_links` maintains).
The algorithm:

```
1. matches = method_a(links, deleted_id, T)
            ∪ method_b(links, T)
            ∪ method_c(live wiki pages, T)   # fallback only, §3.1
2. for each distinct source_page_id (or (c)-only file_path) in matches:
       resolve its file_path (it is a wiki/... page row, or the (c) scan path)
       record WikilinkRewrite(source_page_id, file_path, target_title=T, occurrences=count)
3. wikilinks_to_rewrite = that list   # ONLY these files will be opened for writing (I1)
```

No step enumerates `vault/wiki/` to *find* references except the bounded (c) fallback; the authoritative
source is the index. The number of files written equals `len(wikilinks_to_rewrite)` and the test asserts
no other file is read or written (AC-F13-4a/b).

### 4.3 The dead-link rewrite (AC-F13-1 step 6) — body-only, frontmatter-safe

> **Amendment — DEFECT-F13-002 fix (re-verified 2026-06-29):** The original design claimed "the
> frontmatter block is re-emitted byte-for-byte via `frontmatter.dumps`". That claim was FALSE:
> PyYAML's default `Dumper` reorders keys alphabetically and changes list-item indentation on every
> round-trip. The fix replaced the `frontmatter.loads/dumps` round-trip in the dead-wikilink rewrite
> path with a raw `---` split (`_rewrite_body_preserving_frontmatter`). The `_prune_sources` path
> intentionally mutates `sources[]` content so a full round-trip is unavoidable there, but now uses
> `sort_keys=False` to preserve key order (see §4.4). The description below reflects the as-built
> implementation.

For each `WikilinkRewrite`, F13 opens **only that one file** and rewrites dead links in the
**body only**, keeping the frontmatter block **byte-for-byte identical**:

The helper `_rewrite_body_preserving_frontmatter(raw, target_title)` splits the raw file content
on `---\n` fences (`str.split("---\n", maxsplit=2)`) to isolate the frontmatter block as a raw
string. The frontmatter block is kept **entirely unchanged** — no PyYAML parse, no round-trip,
no re-serialisation. The body (the third segment) is passed to `_rewrite_body`, which applies the
wikilink substitutions:

- `[[T]]`          → `T`
- `[[T|alias]]`    → `alias`   (the displayed text is preserved — Obsidian renders the alias)
- `[[T#section]]`  → `T`
- `[[t]]` / case-or-slug variants caught by method (b) → their displayed text

The replacement is performed with an **anchored regex** over the body string (the same `_WIKILINK_RE`
grammar from `wiki/links.py`, matched against `T` / `slugify(T)`), so it cannot touch a `[[OtherPage]]`
link or any frontmatter key. The function returns `None` when the body is unchanged, preventing a
no-op file write. After writing, F13 re-runs `parse_wikilinks(new_body)` + `persist_links(...)` for
that page so the `links` index reflects the removed link (the row's `dangling`/`target_page_id` no
longer points at the deleted page). Affected `links` rows that previously pointed at the deleted
page have `dangling=True` set (AC-F13-1 step 7) for any link the rewrite did not remove (defensive
— should be none after a clean rewrite).

> **Configurable dead-link form.** The default neutralisation is plain text (display text retained).
> A `CASCADE_DEAD_LINK_STYLE` env (`plain` | `strikethrough`) is reserved; `plain` is the M5 shipped
> behavior and the only one the `test_obsidian_check.py` gate is asserted against. `strikethrough`
> (`~~T~~`) is a post-M5 nicety, not built now.

### 4.4 The `sources[]` prune (preserve branch) — key-order-preserving frontmatter edit

For each page in `will_preserve_with_pruned_source`, F13 opens only that file, loads it with
`frontmatter.loads`, removes `X` from the `sources` list in the metadata dict, re-emits with
`frontmatter.dumps(sort_keys=False)`, and updates `pages.sources` (JSONB) to match — a targeted
edit, never a vault walk. Passing `sort_keys=False` preserves the original YAML key order (fixing
DEFECT-F13-002 for this path); the `sources` value itself changes by design (the pruned entry is
removed), so byte-identical output is impossible and not claimed. Other keys and their values are
unchanged. This page is **not** deleted, its Qdrant point is **not** removed, and it stays in
`index.md`.

---

## 5. Incremental data plane (I1 / I2) and migration need

The applied `cascade_delete` performs, in one pass, in dependency order:

1. **Soft-delete** every page in `will_delete`: `pages.deleted_at = now()` (mirrors `delete_file`,
   ADR-0005). No hard row delete — metadata is retained for audit/cascade.
2. **Qdrant:** `delete_point(page_id)` for each deleted page (hard-delete; soft-deleted pages must not
   surface in F5 search). Reuses the existing primitive (I9).
3. **Wiki rewrites:** apply each `WikilinkRewrite` (§4.3) + each `sources[]` prune (§4.4) — targeted
   writes to **only** the referencing files; re-persist their `links`.
4. **`links` / `edges`:** set `dangling=True` on any residual link rows pointing at a deleted page
   (AC-F13-1 step 7); delete `edges` rows touching a deleted page (the canonical-pair rows on both
   endpoints, via the two endpoint indexes). Edge deletion keeps `GET /graph` honest before the
   recompute; the recompute will re-derive the remainder.
5. **`index.md`:** call `update_index()` **once** — it reads live pages, so the deleted page's entry is
   gone automatically (K3, I1). No hand-edit.
6. **`raw/sources/` file (AQ-v0.5-5):** when the deletion originates from removing a source `X`, delete
   the `raw/sources/X` file from disk so the watcher sees a DELETE (not a CREATE) on restart and **does
   not resurrect** the page. The watcher already ignores `deleted_at IS NOT NULL` rows by construction
   (`_load_page` filters them; the partial-unique index permits the tombstone), so even absent the file
   delete there is no live row — the file delete closes the restart-resurrection gap belt-and-braces.
7. **`bump_version()` EXACTLY ONCE** at the very end, then `_graph_cache.notify_bump(new_version)` — the
   debounced GraphCache recompute fires on its own schedule (I2). F13 **NEVER** calls
   `GraphEngine.recompute()` / FA2 inline (AC-F13-4c/d). The bump is +1 regardless of how many pages or
   files were touched (AC-F13-4c).

**Watcher non-interference (AC-F13-4e).** The wiki rewrites in step 3 are written by the backend
directly to `vault/wiki/`. The watcher observes only `vault/raw/sources/` (its scheduled directory), so
a wiki-file write produces **no** watcher event and **no** re-ingest. The only watcher-visible event is
the step-6 `raw/sources/` DELETE, which routes to `delete_file()` and finds the row already tombstoned
(no-op). The watcher docstring gains an explicit note that soft-deleted rows are never resurrected.

### 5.1 Migration need — **NONE**

F13 reuses `pages.deleted_at` (ADR-0005), `links.dangling`/`links.target_page_id`, the two `edges`
endpoint indexes (already present, comment cites F13), `pages.sources` (JSONB), and `vault_state`. **No
new column, no new table, no Alembic migration.** D2 (`schema.mmd`) is unchanged by F13. (F10 took
Alembic 0009; F9 took 0010; F13 takes none.)

---

## 6. Destructive-op safety — the mandatory dry-run / preview

F13 deletes user data. A **preview is mandatory**, not optional. The design separates **plan** from
**apply** at the function boundary (`plan_cascade_delete` is read-only; `cascade_delete` mutates) and at
the REST boundary, so the UI can always show the human exactly what will happen — including the
`shared_entity_warnings` — **before** any mutation (AC-F13-6a: the confirmation modal lists warnings
before any action).

### 6.1 REST surface (D4)

Mirrors the house DELETE pattern (`DELETE /provider/config/{id}`).

```
POST /pages/{id}/cascade-delete/preview   → 200   (DRY-RUN — read-only, mutates nothing)
{
  "target_page_id": "...",
  "target_title": "...",
  "will_delete": ["...uuid..."],
  "will_preserve_with_pruned_source": ["...uuid..."],
  "wikilinks_to_rewrite": [
    {"source_page_id":"...","file_path":"wiki/concepts/x.md","target_title":"X","occurrences":2}
  ],
  "index_entry_will_be_removed": true,
  "raw_source_to_delete": "raw/sources/x.md",
  "shared_entity_warnings": ["Page 'Y' shares source overlap with 'X'"],
  "match_methods_used": {"wiki/concepts/x.md": "exact"}
}
→ 404 if the page does not exist or is already soft-deleted.

DELETE /pages/{id}                        → 200   (APPLY — single pass)
{
  "deleted_page_id": "...",
  "wikilinks_cleaned": 3,
  "index_entry_removed": true,
  "shared_entity_warnings": ["..."]
}
→ 404 on a non-existent OR already soft-deleted id (idempotent double-delete, AC-F13-5c).
```

`DELETE /pages/{id}` is the canonical destructive endpoint (AC-F13-5). The preview is a **POST**
(it is an action that computes a plan, may be called repeatedly, and shares the `/pages/{id}` namespace)
returning **200**. Both are documented in `docs/api/openapi.json` via `make openapi` (I8). The frontend
(AC-F13-6) calls the preview to populate the confirmation modal, then `DELETE` on confirm; cancel makes
no call.

> **Why not a `?cascade=true` query flag on DELETE.** A separate **preview endpoint** is required for
> the dry-run regardless; given that, a single unambiguous `DELETE /pages/{id}` (always cascade — there
> is no non-cascade delete of a wiki page that keeps the vault valid) is cleaner than a flag whose
> `false` value would produce an invalid vault. Cascade is the only correct delete semantics here, so it
> is the default and only behavior.

---

## 7. D3 — sequence diagram

`docs/sequences/cascade-delete.mmd` (this ADR's companion, S-D3-2 / AC-D3-CD-1) names the phases:
**DELETE /pages/{id} → 3-method reference match (links table; fulltext fallback) → preserve/prune
partition → soft-delete + Qdrant delete → frontmatter-safe dead-wikilink rewrite (targeted writes) →
update_index() → delete raw/sources/ file → bump data_version once → return warnings**, with the
shared-entity warning shown as an explicit note/branch. Rendered by `mmdc` in CI (AC-D3-CI-1).

---

## 8. Do-NOT list (rejection triggers — any one is a P0 block at PR review)

1. **Do NOT full-rescan the vault to find references (I1).** References come from the `links`
   back-reference index (methods a/b). The (c) full-text fallback is bounded, last-resort, scoped to the
   enumerated live-wiki set, and MUST be skipped on the links-table-hit happy path. A blind
   `glob('wiki/**/*.md')` to find links is a reject.
2. **Do NOT call `GraphEngine.recompute()` / FA2 inline (I2).** F13 bumps `data_version` **once** and
   lets the debounced GraphCache recompute. A synchronous FA2 in the delete path is a reject.
3. **Do NOT bump `data_version` more than once** (AC-F13-4c). One delete = one +1, regardless of pages
   or files touched. A bump-per-file is a reject.
4. **Do NOT leave a dead `[[wikilink]]` anywhere (I5).** Every dead link in every referencing file is
   rewritten to plain text (or its alias). `test_obsidian_check.py` 15/15 is the gate; a surviving dead
   link is a reject.
5. **Do NOT corrupt YAML frontmatter (I5).** Dead-wikilink rewrites use a raw `---\n` split
   (`_rewrite_body_preserving_frontmatter`) that keeps the frontmatter block byte-for-byte; the body
   regex never matches inside the `---` fences. `sources[]` prunes use `frontmatter.dumps(sort_keys=False)`
   to preserve key order while mutating only the `sources` value. A global string replace that can touch
   frontmatter keys, or a `frontmatter.dumps` call without `sort_keys=False` on the prune path, is a
   reject.
6. **Do NOT delete a shared page** (AC-F13-2 preserve / AC-F13-3). A page whose `sources[]` retains
   another entry after removing `X` is preserved with a pruned source. Deleting it because it merely
   *referenced* `X` is a reject.
7. **Do NOT skip the dry-run / preview path.** This deletes user data; `plan_cascade_delete` /
   `POST .../preview` is mandatory and read-only. A delete with no preview seam, or a "preview" that
   mutates a store, is a reject.
8. **Do NOT make F13 a loop or an inference call (I7/I6).** It is a single pass over a precomputed plan;
   it resolves no provider and calls no LLM and no FA2. A retry loop or a provider call is a reject.
9. **Do NOT resurrect on restart (AQ-v0.5-5).** Soft-delete the page AND delete the originating
   `raw/sources/` file so the watcher cannot re-create it. Leaving the source file on disk is a reject.
10. **Do NOT hard-delete the `pages` row.** Soft-delete (`deleted_at`) only — metadata is retained for
    audit and the partial-unique index allows a later clean re-create. A `DELETE FROM pages` is a reject.

---

## 9. Architect-review gate (Phase 4 exit)

A F13 PR is approved only if:

1. `cascade_delete` makes **zero** inference calls and **zero** FA2 calls; it bumps `data_version`
   **exactly once** (AC-F13-4c) and `files_written == len(plan.wikilinks_to_rewrite)` (AC-F13-4a/b).
2. The 3-method matcher logs one entry per found reference with its method label; the (c) fallback is
   proven NOT invoked on a links-table-hit fixture (AC-F13-2, §3.1).
3. The preserve-shared rule deletes a page **iff** its `sources[]` becomes empty and prunes the rest
   (AC-F13-2 preserve / AC-F13-3); a shared page is never deleted.
4. Every dead-link rewrite is frontmatter-safe (raw `---` split via `_rewrite_body_preserving_frontmatter`
   — byte-identical frontmatter block, no PyYAML round-trip); `sources[]` prunes use `frontmatter.dumps(sort_keys=False)`
   (key order preserved, only `sources` value mutated); `test_obsidian_check.py` 15/15 green after a
   cascade delete (I5). T-CD-024b (byte-identical gate) and T-CD-025 (prune-on-disk gate) both PASS.
5. `POST /pages/{id}/cascade-delete/preview` mutates nothing (read-only assertion: `data_version`,
   files, Qdrant, rows all unchanged); `DELETE /pages/{id}` returns the AC-F13-5 shape and 404s on
   double-delete.
6. The originating `raw/sources/` file is deleted (AQ-v0.5-5); the watcher does not resurrect.
7. D3 (`cascade-delete.mmd`) renders; D4 (`make openapi`) includes both endpoints; **no D2 change**
   (migration-free). I8 zero-drift.

No invariant is traded for convenience. F13 is placed last in M5 (vault stable) per the PM phase plan.

> **Handoff:** ADR-0026 → tech-writer (formatting + README row). Interface contracts (§2) →
> backend-engineer (`ops/cascade_delete.py`, `DELETE /pages/{id}` + preview) + frontend-engineer
> (confirmation modal with shared-entity warnings, AC-F13-6). D3 `cascade-delete.mmd` → tech-writer.
> Phase 4 verdict → orchestrator.
