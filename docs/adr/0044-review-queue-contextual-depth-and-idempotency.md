# ADR-0044 — Review Queue: contextual depth, stable idempotency & bulk actions (F9 depth pass)

- **Status:** Accepted
- **Date:** 2026-07-01
- **Sprint:** v0.6 (M6 — F9 parity/depth with nashsu/llm_wiki reference)
- **Features:** F9 (Async HITL review queue) · K8 (human curates, LLM maintains) · F10 (Deep Research, seeded queries)
- **Extends (does NOT supersede):** ADR-0034 (F9 proposal model + lazy Create + rule/LLM sweep).
  ADR-0034 remains the authoritative baseline; this ADR adds the four capabilities present in the
  live reference implementation but missing from Synapse today. ADR-0025's F12 parts are untouched.
- **Reference:** R1 (nashsu/llm_wiki) — the live queue the owner inspected holds **517 contextual
  missing-page suggestions**, each a card with a title, a contextual rationale referencing existing
  pages, references to related existing pages, three actions (Deep Research / Create Page / Skip),
  bulk actions (Select pending / Mark resolved / Dismiss / Clear resolved), an FNV-1a
  content-derived stable id, pre-generated `SEARCH:` queries, and a drain-triggered auto-resolve
  sweep. Audit: `docs/reference/llm_wiki-audit/01-AUDIT-FUNZIONALE.md` §F9.
- **Invariants owned:** **K8** (the queue proposes richer, contextual work the human curates) ·
  **I1** (referenced-page resolution and idempotency use bounded indexed reads; the sweep never
  re-scans; Create still writes through `write_wiki_page` — one `data_version` bump) · **I6** (all
  LLM calls still route through `resolve_provider_config`; no new hardcoded backend) · **I7**
  (proposal emission stays ≤1 bounded call; bulk actions are bounded DB writes; no new loop) ·
  **I4** (the grown queue — hundreds of items — stays virtualized; bulk selection is O(visible)) ·
  **I8** (`review_items` schema change → Alembic **0018** + `make er` + `make openapi`).
- **Author:** solution-architect

---

## 1. Context

### 1.1 Where Synapse stands after ADR-0034

ADR-0034 already delivered the hard part of the redesign: proposals (not confirmations), five
types (`missing-page | suggestion | contradiction | duplicate | confirm`), a contextual
`rationale`, lazy on-demand Create through the bounded orchestrated loop, and a rule-then-LLM
auto-resolution sweep. `ops/review.py`, the `review_items` model, migration `0013`, the
`/review/queue/*` routes, and `ReviewQueueView.tsx` are all live and green.

### 1.2 What the live nashsu queue has that Synapse does not

The owner inspected the reference queue live (517 cards). Four concrete capabilities are present
there and absent in Synapse — each is a depth gap, not a redesign:

1. **Stable, content-derived idempotency id.** nashsu derives each review id from an **FNV-1a hash
   of the item's content** (type + title + rationale-defining fields), so the *same* proposal
   re-emitted across re-ingests keeps its id — and therefore its `resolved`/`skipped`/`dismissed`
   status. Synapse's `enqueue_review` uses `uuid.uuid4()` per emission and is documented as an
   "event log, not a per-page singleton" (ADR-0034 §3.2). **Consequence:** re-ingesting the same
   sources re-proposes items the human already skipped, and duplicate cards accumulate. At the 517
   scale the owner saw, an event-log model would produce thousands of dupes. **This is the single
   most important gap** — it is what makes a large queue livable.

2. **References to related existing pages (plural).** nashsu cards cite *the existing pages the
   suggestion is contextually about* ("this concept connects [[X]], [[Y]], [[Z]]"). Synapse stores
   only a single `page_id` (the conflict/target page for contradiction/duplicate). A `missing-page`
   or `suggestion` card today shows a rationale string but cannot render the related-page links the
   reference cards carry.

3. **Pre-generated `SEARCH:` queries per item.** nashsu emits 1–3 search queries at ingest time and
   uses the first as the Deep Research seed (audit §F9: prompt imposes a `SEARCH:` line, parsed into
   `searchQueries`, consumed by `queueResearch`). ADR-0034 **dropped** `pre_generated_query` and
   derives the Deep Research topic from `proposed_title`/`rationale` instead. This works but loses
   the *curated* seed query and the ability to show the user what will be searched. The owner
   explicitly lists pre-generated search queries as a desired feature.

4. **Bulk actions + a `dismissed`/resolved visibility model.** nashsu has "Select pending", "Mark
   resolved", "Dismiss", and "Clear resolved". Synapse has per-item Create/Skip/Deep-Research and a
   single global "Sweep" button — no multi-select, no dismiss-vs-resolve distinction, no
   clear-resolved. At 517 items, per-item-only triage is unusable.

### 1.3 Ground truth this ADR builds on (unchanged seams)

- **`ops/review.py`** — `enqueue_review`, `propose_reviews`, `sweep_reviews`,
  `create_page_from_review`, `list_queue`, `skip`, `deep_research`, `_set_status`. This ADR extends
  these; it introduces no new module.
- **`ReviewItem`** model + migration `0013`. This ADR adds columns via **0018**.
- **`/review/queue/*`** routes in `main.py` + `ReviewItemResponse`. This ADR extends the projection
  and adds two bulk routes.
- **`ReviewQueueView.tsx`** + `reviewStore.ts` + `reviewClient.ts`. This ADR adds selection state,
  bulk action bars, referenced-page links, and a search-query display.
- **`resolve_provider_config("ingest", vault_id)` + `run_orchestrated_loop`** — the I6/I7 seams.
  Unchanged: the search-query emission rides the **same single** proposal call (no new LLM call).
- **`Link` / `Page` tables** — the bounded indexed reads for referenced-page resolution and
  dangling-link detection already used by `propose_reviews`.

---

## 2. Decision summary

1. **Add a stable content-derived idempotency key `content_key`** (a 16-hex FNV-1a-style digest over
   `vault_id + item_type + normalized(proposed_title) + target_page_title-or-page_id`) with a
   **partial unique index** on `(vault_id, content_key)` scoped to *live* statuses. `enqueue_review`
   becomes an **UPSERT-on-`content_key`**: a re-emitted proposal whose `content_key` already exists
   is a no-op if the existing row is terminal (`skipped`/`dismissed`/`created`/`auto_resolved`) and a
   touch-`created_at`/refresh-`rationale` if it is still `pending`. This makes the queue
   **idempotent across re-ingest** — the nashsu behavior. The "event log" note in ADR-0034 §3.2 is
   **superseded by this ADR** for the missing-page/suggestion/duplicate/contradiction cases.

2. **Add `referenced_page_ids` (JSON array of page-id strings)** to carry the related existing pages
   a proposal is contextually about (plural). The proposal LLM call returns a
   `referenced_page_titles` list; `propose_reviews` resolves them to ids via a bounded indexed
   `pages` read (same pattern as the existing `target_page_title` resolution) and stores the
   resolved ids. The `page_id` single-target column is **kept** for contradiction/duplicate (the
   *primary* conflict). `referenced_page_ids` is the *context set*.

3. **Re-introduce `search_queries` (JSON array of ≤3 strings)** produced by the **same** single
   bounded proposal call (no new call — the prompt gains a `search_queries` field per proposal). The
   Deep-Research action seeds its topic from `search_queries[0]` when present, falling back to the
   ADR-0034 order (`proposed_title → rationale → page.title`). The UI shows the queries on the card.
   This restores the dropped `pre_generated_query` capability in a **plural, structured** form
   without an extra provider call.

4. **Add a `dismissed` status and bulk actions.** New status value `dismissed` (human hid the item
   without acting — distinct from `skipped`, which is "considered and declined", and from
   `auto_resolved`). New bounded endpoints `POST /review/queue/bulk` (apply skip/dismiss/mark-resolved
   to a capped list of ids) and `DELETE /review/queue/resolved` (hard-delete terminal rows —
   "Clear resolved"). "Select pending" and "Mark resolved" are **UI selection + the bulk endpoint**;
   no new server concept.

5. **`GET /review/queue` gains a `status` filter** (default: the live set) so the UI can show
   Pending / Resolved / Dismissed tabs without pulling everything. Paging stays capped (I7).

6. **No new LLM call, no new loop, no new module, no C4 topology change.** The proposal call count is
   unchanged (still ≤1 per ingest run). Bulk actions are bounded DB writes. Idempotency and
   referenced-page resolution are bounded indexed reads (I1). Create is untouched (still the bounded
   loop + `write_wiki_page`, one `data_version` bump).

---

## 3. Data model changes (migration 0018)

Next free migration is **0018** (0013 = ADR-0034 proposal model; 0014 = lint; 0015/0016/0017 =
vault_state config). The `ReviewItem` model is extended; `make er` regenerates `docs/er/schema.mmd`
and `make openapi` regenerates `docs/api/openapi.json` (I8).

### 3.1 Column changes

| Column | Action | Type / nullability | Notes |
|--------|--------|--------------------|-------|
| `content_key` | **add** | Text, **nullable** | 16-hex stable digest (§3.2). Nullable so `confirm` items (which are intentionally *not* deduped — every "please confirm X" is a distinct human ask) and legacy rows can carry NULL. |
| `referenced_page_ids` | **add** | `JSONB().with_variant(JSON,"sqlite")`, nullable | Array of page-id **strings** (resolved existing pages the proposal is contextually about). Bounded (`≤ REVIEW_REFERENCED_PAGES_MAX`, default 8). NULL/`[]` when none. Distinct from `page_id` (single primary conflict) and `source_page_id` (provenance). |
| `search_queries` | **add** | `JSONB().with_variant(JSON,"sqlite")`, nullable | Array of ≤3 pre-generated search-query strings (§2.3). NULL when the model produced none. Seeds Deep Research; shown on the card. |
| `status` | **extend (value set only)** | Text, not null | New value `dismissed` added to the enum-by-convention set: `pending \| created \| skipped \| dismissed \| deep_researched \| auto_resolved`. No type change (Text stays). |
| `resolution` | **extend (value set only)** | Text, nullable | New value `dismissed` added: `created \| skipped \| dismissed \| researched \| rule_resolved \| llm_resolved`. |

All existing columns (`id`, `vault_id`, `item_type`, `page_id`, `source_page_id`, `proposed_*`,
`rationale`, `created_page_id`, `deep_research_run_id`, timestamps) are **unchanged**.

### 3.2 `content_key` derivation (stable idempotency)

```
content_key = fnv1a_16hex(
    vault_id + "\x1f" +
    item_type + "\x1f" +
    normalize(proposed_title) + "\x1f" +          # lower, collapse ws; "" if None
    (normalize(target_page_title) or page_id or "")  # the conflict anchor for contradiction/duplicate
)
```

- `normalize()` is the existing `_normalize_title` (lower + collapse whitespace) — reused, not
  reinvented.
- **FNV-1a is chosen** (not sha256) to match the reference and because the key is a *dedup handle*,
  not a security digest — a fast 64-bit FNV-1a rendered as 16 hex chars is ample and collision-safe
  at vault scale. A pure-Python one-liner; **no new dependency** (I9).
- **`confirm` items get `content_key = NULL`** (not deduped): a confirmation request is a distinct
  human ask each time and must not be silently coalesced.
- The key is **content-addressed, not row-addressed** — the same logical proposal from two different
  ingest runs collides by design. That is the whole point.

### 3.3 Idempotency index

Add a **partial unique index** (Postgres) / filtered index (SQLite emulated via a guarded upsert):

```
ix_review_items_vault_content_key_live  UNIQUE (vault_id, content_key)
    WHERE content_key IS NOT NULL AND status IN ('pending')
```

- Uniqueness is scoped to **`pending`** live rows: a terminal (`skipped`/`dismissed`/`created`/
  `auto_resolved`) row with the same `content_key` **still blocks re-proposal** (see §3.4 upsert
  logic) but does not conflict with a new pending row after the human has, e.g., re-opened it — the
  upsert reads the terminal row first and no-ops. On SQLite (unit tests) the partial-unique index is
  emulated by the upsert's read-before-write (the DB-level guarantee is Postgres-only; the
  application upsert is the portable contract — mirrors the raw-SQL portability note in project
  memory).

### 3.4 `enqueue_review` becomes an idempotent upsert

`enqueue_review` gains the new fields and, when `content_key` is non-NULL, performs a **read-by-key
then branch** (bounded, single indexed read — I1):

```
existing = SELECT ... WHERE vault_id=? AND content_key=? ORDER BY created_at DESC LIMIT 1
if existing is None:
    INSERT new pending row                                   # first sighting
elif existing.status == 'pending':
    UPDATE existing SET rationale=?, referenced_page_ids=?,  # refresh context, keep id+created_at
                        search_queries=?                     # (the human hasn't acted yet)
else:  # terminal: skipped / dismissed / created / auto_resolved
    NO-OP                                                    # respect the human's prior decision
```

`content_key=NULL` (i.e. `confirm`) always INSERTs (no dedup). This is the exact nashsu semantics:
**a proposal the human already disposed of never comes back; an un-acted proposal is refreshed in
place, keeping its id and queue position.**

### 3.5 Migration 0018 notes

- Additive: ADD `content_key`, `referenced_page_ids`, `search_queries` (all nullable); ADD the
  partial-unique index. No type alteration (`status`/`resolution` stay Text). No column drops.
- **Backfill:** `content_key` is left NULL for pre-existing rows (they are historical; not
  re-deduped retroactively). M6 is single-operator pre-release — acceptable. Optionally the migration
  MAY compute `content_key` for existing `pending` non-`confirm` rows in one bounded pass so the
  dedup takes effect immediately; document whichever is chosen.
- **`make er` (I8):** `review_items` gains three columns; no new FK (the referenced ids are a JSON
  array, deliberately *not* a join table — see §8 risk 2). Regenerate `docs/er/schema.mmd`.

---

## 4. Generation — where the new fields come from (I6/I7, both routes)

### 4.1 Orchestrated route (API / Local) — the single proposal call, enriched

`propose_reviews` (`ops/review.py`) is unchanged in **structure**: still one anti-spam gate, still
**≤1** `InferenceProvider` call (operation `"ingest"`, resolved via `resolve_provider_config` — I6),
still capped at `_PROPOSE_MAX_ITEMS`, still fire-and-forget and degrade-safe (I7). Three enrichments,
**all inside the existing call** (no new call, no loop):

1. **Prompt gains two per-proposal fields** in the returned JSON:
   - `referenced_page_titles`: ≤ `REVIEW_REFERENCED_PAGES_MAX` existing-vault titles this proposal
     is contextually about (the model is given the bounded existing-title list already — §5 of
     ADR-0034 — so it references real pages, not invented ones).
   - `search_queries`: ≤3 short web-search queries that would advance this item (the nashsu `SEARCH:`
     line, now structured per proposal).
2. **`ProposalDTO` gains** `referenced_page_titles: list[str]` and `search_queries: list[str]`.
   `_parse_proposals` extracts them tolerantly (drop non-strings; cap lengths).
3. **`propose_reviews` resolves** `referenced_page_titles → referenced_page_ids` via a bounded
   indexed `pages` read (reuse the exact `target_page_title` lookup pattern already in the function;
   drop titles that don't resolve to a live page — the model must not fabricate references), computes
   `content_key` (§3.2), and passes all three to `enqueue_review` (now an upsert).

The **rule-based** proposals (dangling wikilinks, not-written suggested pages) also get a
`content_key`; their `referenced_page_ids` is `[the written page that referenced the target]` when
known, and `search_queries` is `[proposed_title]` as a trivial seed (no LLM needed).

### 4.2 Delegated route (CLI) — closing ADR-0034 §9 risk 1 (bounded, opt-in)

ADR-0034 §9 flagged that the delegated (CLI) branch emits **no** proposals because the orchestrator
does not enumerate the pages the CLI agent writes via MCP `write_page`. This ADR closes that gap
**without** adding a second agent loop and **without** a provider-specific branch (I6):

- The MCP `write_page` tool (`mcp/server.py`, which already reuses the ingest write primitives)
  **records the titles/ids it writes for the current delegated run** on the existing run context
  (an in-memory list keyed by the run — no new table). This is a pure side-record; it does not
  change the delegated agent's behavior and is capability-agnostic (any agentic provider that writes
  through `write_page` gets it — I6, no isinstance).
- After the delegated run finishes, the orchestrator calls the **same** `propose_reviews(...)` seam,
  passing the recorded written pages as `written_pages` and a **synthesized `Analysis`** built from
  the recorded titles (no `suggested_pages` — so the LLM proposal path runs on the written set, the
  rule-based dangling-link path runs on the written pages' `links` rows exactly as for orchestrated).
  One bounded call, same caps, same fire-and-forget degrade (I7).
- If the recorded set is empty (agent wrote nothing, or a provider that bypasses `write_page`),
  `propose_reviews` early-returns (its existing `if not written_pages` guard) — zero cost, no
  proposals. **This is opt-in by construction:** only providers that write through the MCP tool get
  proposals; nothing is hardcoded.

This is a **strict addition** — it introduces no new invariant surface (the proposal call is the
same bounded call). It is flagged as the one genuinely new integration point and gets its own tests
(§9 risk 1 is downgraded from "reserved" to "delivered, bounded, opt-in").

### 4.3 Bounds (I7) — unchanged surface

- Proposal emission: still **≤1** provider call per run; `content_key`/reference resolution are
  bounded indexed reads; the per-proposal `referenced_page_titles` and `search_queries` are truncated
  at parse (`≤ REVIEW_REFERENCED_PAGES_MAX`, ≤3). No new timeout, no new budget — same as ADR-0034.
- New config knobs: `REVIEW_REFERENCED_PAGES_MAX` (default 8), `REVIEW_SEARCH_QUERIES_MAX` (default 3),
  `REVIEW_BULK_MAX_IDS` (default 200 — the bulk-action cap, §6). Added to `config.py` with defaults;
  DEPLOY.md documents them (tech-writer).

---

## 5. Auto-resolve sweep — idempotency-aware, otherwise unchanged

The sweep (`sweep_reviews`, ADR-0034 §6) is **structurally unchanged**: Pass-1 rule-based
(title-now-exists) + Pass-2 conservative bounded LLM (default-to-keep, never-resolve-`confirm`), both
bounded (I7), never re-scanning (I1). Two small alignments with the new model:

1. **Dismissed rows are terminal** and are never re-examined by the sweep (they filter on
   `status='pending'` already — no change needed, just noted).
2. **Idempotency makes the sweep lighter, not different.** Because re-ingest no longer re-creates
   already-resolved proposals (§3.4), the sweep's Pass-1 candidate set shrinks naturally over time —
   the sweep now only ever sees genuinely-still-open items. No behavioral change to the sweep code;
   this is a consequence of §3, recorded for the reviewer.

The sweep's human-gate (K8) is unchanged: `contradiction`/`suggestion`/`confirm` are preserved unless
the conservative LLM is confident; `confirm` is never auto-resolved.

---

## 6. REST API changes (D4)

Base path `/review/queue` unchanged. `make openapi` regenerates `docs/api/openapi.json` (I8).

| Method + path | Body / params | Success | Notes |
|---------------|---------------|---------|-------|
| `GET /review/queue` | `?vault_id&status&limit&offset` (limit default 50, max 200 — I7) | 200 `{items:[…], total, limit, offset}` | **New `status` filter** (`pending`(default) \| `resolved` \| `dismissed` \| `all`). `resolved` = the terminal-resolved set (`created`/`auto_resolved`/`deep_researched`). Items carry the §6.1 enriched projection. |
| `POST /review/queue/{id}/create` (+ `/approve` alias) | — | 201 `ReviewItem` | **Unchanged** (ADR-0034 §5). Lazy on-demand Create, one `data_version` bump. |
| `POST /review/queue/{id}/skip` | — | 200 `ReviewItem` | Unchanged. `status=skipped`. |
| `POST /review/queue/{id}/dismiss` | — | 200 `ReviewItem` | **New.** `status=dismissed`, `resolution=dismissed`. Distinct from skip (hidden, not "declined after consideration"). |
| `POST /review/queue/{id}/deep-research` | — | 202 `{review_item_id, run_id}` | **Topic now seeds from `search_queries[0]`** when present, else the ADR-0034 order. 503 if `SEARXNG_URL` unset. |
| `POST /review/queue/bulk` | `{vault_id, action: "skip"\|"dismiss"\|"mark-resolved", ids: [uuid,…]}` | 200 `{updated, skipped_terminal}` | **New.** Bounded bulk status write (`len(ids) ≤ REVIEW_BULK_MAX_IDS`, 400 otherwise — I7). `mark-resolved` → `status=auto_resolved, resolution=llm_resolved`-style terminal (human-marked). Only `pending` ids are updated; already-terminal ids are counted in `skipped_terminal`, never re-mutated. No provider call. |
| `POST /review/queue/sweep` | `?vault_id` | 200 `{rule_resolved, llm_resolved, kept}` | Unchanged (ADR-0034 §6). |
| `DELETE /review/queue/resolved` | `?vault_id` | 200 `{deleted}` | **New — "Clear resolved".** Hard-deletes terminal rows (`skipped`/`dismissed`/`created`/`auto_resolved`/`deep_researched`) for the vault, capped and bounded (delete in one bounded statement). Pending rows are never touched. Idempotent. |
| (all action routes) | unknown `id` | 404 | Unchanged. |

### 6.1 `ReviewItem` JSON projection (additions in **bold**)

```
{
  id, vault_id, item_type, status,
  proposed_title, proposed_page_type, proposed_dir, rationale,
  page_id, page_title?,
  source_page_id, created_page_id,
  resolution, deep_research_run_id,
  content_key,                       // **new** — stable dedup handle (opaque to UI)
  referenced_page_ids,               // **new** — array of page-id strings
  referenced_pages?,                 // **new** — [{id,title,type}] convenience join for the card
  search_queries,                    // **new** — array of ≤3 strings (Deep-Research seeds)
  created_at, reviewed_at
}
```

`referenced_pages` is a bounded convenience join (`pages` lookup for the ≤8 ids) so the card can
render `[[title]]` links without a second round-trip — same pattern as the existing `page_title`.

---

## 7. Frontend changes (I3/I4)

`ReviewQueueView.tsx` + `reviewStore.ts` + `reviewClient.ts`. No new framework, no WYSIWYG (I4),
CodeMirror is not involved (this is a card list). Changes:

1. **Card enrichment (per-row):**
   - Render `referenced_pages` as a wrapped row of clickable `[[title]]` chips (click →
     `setActiveSection("pages")` + select the page — reuse the existing conflict-page link handler).
   - Render `search_queries` as a small muted "will search: q1 · q2" line under the rationale (shown
     for items that carry them).
   - Row height grows; the virtualizer `estimateSize` is bumped and `measureElement` used for
     variable heights (still virtualized — I4; hundreds of rows stay smooth).
2. **Selection + bulk bar:**
   - A per-row checkbox (visible in a "select mode") and a header **"Select pending"** toggle that
     selects all *currently loaded* pending rows (O(visible) — the store holds only the loaded page).
   - A bulk action bar (appears when ≥1 selected): **Mark resolved · Dismiss · Skip**, each calling
     `POST /review/queue/bulk` with the selected ids, then refreshing.
   - **"Clear resolved"** button in the header (visible on the Resolved/Dismissed tab) →
     `DELETE /review/queue/resolved`.
3. **Status tabs:** Pending (default) · Resolved · Dismissed, mapped to the `GET ?status=` filter.
   The pending count badge is unchanged.
4. **I3 compliance:** all new state (selection set, active tab) lives in `reviewStore` behind
   selectors + `useShallow`; no unrelated re-renders. Selection is a `Set<string>` in the store;
   the row reads only its own membership via a selector.

The three primary actions (Create / Deep Research / Skip) and their per-item error handling are
**unchanged** from ADR-0034.

---

## 8. Invariant compliance

| Inv | How this design guarantees it |
|-----|-------------------------------|
| **K8** | The queue now proposes *contextually richer* work (referenced pages + seed queries) and lets the human triage it *at scale* (bulk + dismiss + clear). Pages are still created only on explicit Create. The human curates; the LLM proposes. Idempotency means the human's decisions **persist** — a skipped item stays gone. |
| **I1** | `content_key` upsert and `referenced_page_title` resolution are **bounded single indexed reads** (new partial-unique index + the existing title index). The sweep is unchanged (no re-scan). Create is untouched — one `write_wiki_page`, one `data_version` bump. Bulk actions are bounded DB writes. No FA2, no vault re-scan anywhere. |
| **I2** | Untouched. No graph layout work; referenced pages come from `pages` reads, not the graph. |
| **I4** | The grown queue (hundreds of items — the 517 the owner saw) stays TanStack-virtualized; variable row heights use `measureElement`; selection is O(loaded). No un-virtualized long list. |
| **I6** | The enriched proposal still rides the **same single** `resolve_provider_config("ingest")` call — no new call, no new ABC method, no isinstance/provider_type branch. The delegated-route proposal (§4.2) uses the **same** `propose_reviews` seam via the capability-agnostic MCP write-record — no hardcoded backend. Bulk/dismiss/clear are provider-free DB writes. |
| **I7** | Proposal emission: still **≤1** provider call, same caps + timeout + cost logging; new per-proposal lists truncated at parse. Bulk: `len(ids) ≤ REVIEW_BULK_MAX_IDS` (400 otherwise). Clear-resolved: one bounded DELETE. No new loop anywhere; the sweep's bounds are unchanged. |
| **I8** | `review_items` change → Alembic **0018** + `make er` (three new columns) + `make openapi` (the new/changed routes + projection). D3 ingest sequence gains the delegated-route proposal step (tech-writer). No sprint-done without these artifacts consistent. |

No invariant is traded. The genuine tensions are in §9.

---

## 9. Flagged tensions & risks

1. **Delegated-route proposals are new integration surface.** §4.2 adds a write-record on the MCP
   `write_page` path and a post-run `propose_reviews`. Risk: an agentic provider that writes pages by
   some path *other* than `write_page` still emits nothing. **Accepted** — that is the correct
   conservative behavior (empty set → early return, zero cost); it is opt-in by construction and
   documented, not silent. Tests cover: write-record populated → proposals emitted; empty → no call.

2. **`referenced_page_ids` is a JSON array, not a join table.** This avoids a `review_referenced_pages`
   junction and its cascade-on-page-delete complexity. Cost: a deleted referenced page leaves a stale
   id in the array. **Mitigation:** the `referenced_pages` convenience join drops ids that no longer
   resolve to a live page (render-time filter — same tolerance as dangling wikilinks). The array is a
   *display + context hint*, never a source of truth or an FK obligation. Recorded as a deliberate
   simplification (I9 — do not over-engineer).

3. **Idempotency vs. legitimately-recurring proposals.** A `content_key` collision suppresses
   re-proposal even if the underlying context changed. **Mitigation:** the key includes
   `item_type + title + conflict-anchor`, so a materially different proposal (different title/anchor)
   gets a different key. For `confirm`, dedup is disabled entirely (§3.2). A pending row's `rationale`
   is *refreshed* on re-emission (§3.4), so context stays current without a new card. The window this
   misses (same title, changed nuance, already-skipped) is acceptable and re-openable by the human.

4. **`dismissed` vs `skipped` semantics must be documented** or users conflate them. USER.md
   (tech-writer) states: **Skip** = "considered and declined, keep it out of the way"; **Dismiss** =
   "hide this, I'm not acting"; **Mark resolved** = "this is handled (elsewhere)". All three are
   terminal; all three are cleared by "Clear resolved". This is a UX-clarity risk, not a technical one.

5. **Clear-resolved is a hard delete.** Unlike page soft-delete (ADR-0005), terminal review rows are
   hard-deleted (they are advisory metadata, not vault content). **Accepted** — they carry no vault
   state; `created_page_id` points at a page that persists independently. Bounded, idempotent, vault-
   scoped. No cascade risk (the `pages` FK is nullable and the page is not deleted).

6. **FNV-1a is not cryptographic.** Intentional (§3.2) — it is a dedup handle, not a security or
   integrity digest. Collision probability at vault scale (thousands of items) is negligible for a
   64-bit hash; a collision would merely coalesce two proposals, a benign failure mode.

---

## 10. Do-NOT list (rejection triggers)

A PR touching this depth pass is **rejected on review** if it:

1. **DO NOT** add a second provider call for referenced pages or search queries — they ride the
   **one** existing proposal call (I6/I7). The delegated route uses the **same** `propose_reviews`
   seam, not a new agent loop.
2. **DO NOT** make `enqueue_review` a blind INSERT for non-`confirm` items — it MUST upsert on
   `content_key` and respect terminal rows (§3.4). A re-ingest must not resurrect a skipped/dismissed
   item.
3. **DO NOT** hardcode a backend/model, or branch on isinstance/provider_type for the delegated
   proposal path — resolve via `resolve_provider_config("ingest")`; empty write-record → no call, not
   a substituted provider (I6).
4. **DO NOT** create a `review_referenced_pages` junction table or a hard FK per referenced id —
   `referenced_page_ids` is a bounded JSON array; stale ids are filtered at render (§9.2, I9).
5. **DO NOT** let `POST /review/queue/bulk` or `DELETE /review/queue/resolved` operate unbounded —
   `ids` capped at `REVIEW_BULK_MAX_IDS`; clear-resolved is one bounded vault-scoped statement (I7).
6. **DO NOT** touch pending rows in "Clear resolved", or auto-resolve `confirm` in any bulk path
   (only explicit human `mark-resolved` on a specific id may terminalize it; the sweep still never
   touches `confirm`).
7. **DO NOT** re-scan the vault or call FA2 for referenced-page/idempotency resolution — bounded
   indexed reads only (I1/I2).
8. **DO NOT** un-virtualize the review list to fit checkboxes/tabs, or select more than the loaded
   page in "Select pending" (I4 — O(loaded)).
9. **DO NOT** ship the schema change without Alembic **0018** + `make er` + `make openapi`
   regenerated (I8).
10. **DO NOT** dedup `confirm` items (their `content_key` is NULL by design — every confirmation is a
    distinct human ask).

---

## 11. Implementation plan (phased, each phase independently shippable)

> **Routing rule:** **[AI]** = ai-agent-engineer (touches the `InferenceProvider` proposal call);
> **[BE]** = backend-engineer; **[FE]** = frontend-engineer. Each phase lands behind green tests;
> QA + tech-writer gate as usual. Phases are ordered so each is shippable alone.

### Phase A — Idempotency (the highest-value, standalone) — [BE] + [AI]

- **[BE]** Migration **0018**: ADD `content_key`, `referenced_page_ids`, `search_queries`
  (nullable); ADD `ix_review_items_vault_content_key_live` partial-unique index; extend the
  `status`/`resolution` value-set docs (add `dismissed`). Update the model docstring. Run `make er`
  → commit `docs/er/schema.mmd`.
- **[BE]** `_content_key(...)` helper (FNV-1a, reuse `_normalize_title`); `confirm` → NULL.
- **[BE]** `enqueue_review` → upsert-on-`content_key` (§3.4): read-by-key, INSERT / refresh-pending /
  no-op-terminal. Keep the pure-DB-write contract (no provider).
- **[AI]** `propose_reviews`: compute `content_key` for both rule-based and LLM proposals before
  enqueue (the LLM prompt/DTO enrichment is Phase B; here every emitted proposal just gains a key).
- **Tests:** re-ingesting the same source twice yields **one** pending row per proposal (not two);
  a skipped item is not resurrected on re-ingest; a pending item's rationale refreshes in place
  keeping its id; `confirm` still inserts every time.
- **Ships:** the queue stops accumulating duplicates — the single biggest livability win — with no
  UI change required.

### Phase B — Contextual references + search queries — [AI] + [BE]

- **[AI]** Extend the proposal prompt + `ProposalDTO` + `_parse_proposals` with
  `referenced_page_titles` (≤`REVIEW_REFERENCED_PAGES_MAX`) and `search_queries` (≤3); tolerant parse.
- **[BE]** `propose_reviews`: resolve `referenced_page_titles → referenced_page_ids` via the existing
  bounded `pages` lookup (drop non-resolving titles); persist both arrays through the (already
  upserting) `enqueue_review`. Rule-based proposals get `[referencing page]` + `[proposed_title]`.
- **[BE]** `deep_research` action: seed topic from `search_queries[0]` when present (fallback to the
  ADR-0034 order).
- **[BE]** `config.py`: `REVIEW_REFERENCED_PAGES_MAX`, `REVIEW_SEARCH_QUERIES_MAX`.
- **Tests:** proposals carry resolved referenced ids (invented titles dropped); Deep-Research uses the
  seed query; still exactly one provider call; caps enforced.
- **Ships:** cards gain context; Deep Research seeds from curated queries. No UI required to be
  correct (projection carries the new fields; UI render is Phase D).

### Phase C — Bulk actions + status filter + dismiss + clear-resolved — [BE]

- **[BE]** `dismiss` op + route; `POST /review/queue/bulk` (bounded, `REVIEW_BULK_MAX_IDS`);
  `DELETE /review/queue/resolved` (bounded); `GET /review/queue?status=` filter; `config.py`
  `REVIEW_BULK_MAX_IDS`. Extend `ReviewItemResponse` projection (§6.1). Run `make openapi` → commit.
- **Tests:** bulk caps (400 over limit); bulk only mutates pending ids; clear-resolved never touches
  pending; status filter partitions correctly; `dismiss` is terminal.
- **Ships:** server-side triage at scale, independently of the UI.

### Phase D — Frontend depth — [FE]

- **[FE]** Referenced-page chips + search-query line on the card (variable-height virtualization via
  `measureElement`); selection `Set` + "Select pending" toggle + bulk bar (Mark resolved / Dismiss /
  Skip → bulk endpoint); status tabs (Pending / Resolved / Dismissed); "Clear resolved" button. All
  new state in `reviewStore` behind selectors + `useShallow` (I3). i18n keys (IT/EN).
- **Tests (vitest):** bulk selection + dispatch; tab switch re-fetches with `?status=`; card renders
  referenced chips + queries; virtualization holds at N=500 rows.
- **Ships:** the full nashsu-depth UI.

### Phase E — Delegated-route proposals — [AI] + [BE]

- **[BE]** MCP `write_page` records written page ids/titles per delegated run (in-memory run context,
  no table); orchestrator calls `propose_reviews` with the recorded set + a synthesized `Analysis`
  after the delegated run; empty set → early return.
- **[AI]** Verify the proposal call path is identical (same bounded call, same degrade); no
  provider-specific branch.
- **Tests:** delegated run that writes via `write_page` → proposals emitted (bounded, one call);
  delegated run that writes nothing → no call; no isinstance/provider_type branch (static check).
- **Ships:** closes ADR-0034 §9 risk 1; CLI ingests now populate the queue.

### 11.1 Cross-cutting (handoffs)

- **tech-writer:** this ADR (format + README row + `docs/adr/README.md` index line); USER.md (the
  Skip/Dismiss/Mark-resolved/Clear-resolved verbs + referenced pages + seed queries); DEPLOY.md (the
  new `REVIEW_*` env vars); D3 ingest sequence (add the delegated-route proposal step). C4: **no new
  container/component** — `ops/review.py` stays a component inside the FastAPI service; no D1 change.
- **qa-test-engineer:** §10 Do-NOT list is the rejection gate; the §11 per-phase tests are the
  milestone gate. Idempotency (Phase A) is the mandatory pre-merge check (re-ingest → no dupes).
- **orchestrator:** route [AI] items to ai-agent-engineer, [BE] to backend-engineer, [FE] to
  frontend-engineer; ship phases in order (A → E); human checkpoint before merge.

---

## 12. Sign-off

**APPROVED to implement.** This depth pass extends ADR-0034 (it does not supersede it) to reach the
contextual depth of the live nashsu/llm_wiki queue: a **stable content-derived `content_key`** makes
the queue idempotent across re-ingest (skipped/dismissed items stay gone; pending items refresh in
place — the key to a 517-item queue being livable); **`referenced_page_ids`** carries the related
existing pages each card is about; **`search_queries`** restores curated Deep-Research seeds via the
**same single** proposal call; and **bulk actions + `dismissed` + clear-resolved + status tabs** make
triage at scale usable. The delegated (CLI) route finally emits proposals through the **same** bounded
`propose_reviews` seam. No new provider call, no new loop, no new module, no C4 change. I1, I4, I6,
I7, I8 are satisfied; K8 is deepened (the human curates *at scale*, decisions persist). Schema ships
as Alembic **0018** with `make er` + `make openapi` regenerated. Phases A–E are each independently
shippable, A (idempotency) first.

> Handoff: ADR-0044 → tech-writer (format, README row + index, USER/DEPLOY, D3 delegated-proposal
> step). Interface contracts (§3–§7) → backend-engineer [BE] + ai-agent-engineer [AI] +
> frontend-engineer [FE] per §11. PR verdicts → orchestrator. This ADR extends ADR-0034 (the F9
> proposal model stands; this adds contextual depth, idempotency, and bulk triage).
