# ADR-0034 — Review Queue: proposal model with lazy on-demand page creation (F9 redesign)

- **Status:** Accepted
- **Date:** 2026-06-30
- **Sprint:** v0.5 (M5 — F9 follow-up / parity correction)
- **Features:** F9 (Async HITL review queue) · K8 (human curates, LLM maintains)
- **Supersedes:** the **F9 review-queue parts** of ADR-0025 (§3.1–§3.6, §5 F9 items, §6 F9 row,
  §7 risks 1/2/5, §8 sign-off conditions 1–3). ADR-0025's **F12** parts (multi-format ingest)
  are untouched and remain authoritative.
- **Reference:** R1 (nashsu/llm_wiki) — the proposal/lazy-create review pattern this aligns to.
- **Invariants owned:** **K8** (HEADLINE — the queue now proposes work the human approves, not
  no-op confirmations) · **I1** (Create writes incrementally through the existing wiki-write seam;
  the auto-resolution sweep never re-scans) · **I6** (every new LLM call routes via
  `resolve_provider_config` — never a hardcoded backend) · **I7** (proposal emission, on-demand
  Create, and the LLM sweep are each bounded by `max_iter` + `token_budget`, cost logged) ·
  **I8** (`review_items` schema change → new Alembic migration + `make er` regenerate + OpenAPI)
- **Author:** solution-architect

---

## 1. Context

### 1.1 The defect in the ADR-0025 review model

ADR-0025 shipped F9 as an **advisory log of already-created pages**. The orchestrator, after
writing each wiki page, made **one bounded provider call per page** to generate 1–3 follow-up
**questions** (`generate_review_queries`), then enqueued a `new_page` review item carrying those
questions in `pre_generated_query`. The three actions were:

- **Approve** → `status=approved`, `reviewed_at=now()`. **A pure no-op** — the page already
  exists on disk; Approve confirms nothing actionable (`ops/review.py::approve`).
- **Skip** → `status=skipped`.
- **Deep-Research** → delegate to F10 with the first follow-up question.

This diverges from the reference implementation (R1, nashsu/llm_wiki) on two counts:

1. **Approve is meaningless.** The human is asked to "approve" a page that was created without
   their consent. K8 ("human curates, LLM maintains") is inverted: the LLM curates (auto-creates
   everything), the human rubber-stamps.
2. **The queue is question-spam, not proposals.** Every ingested page emits an item regardless of
   whether there is genuinely useful follow-up work. The items carry questions, not actionable
   units of work (a page to create, a conflict to resolve, a duplicate to merge).

The owner (Emanuele) has explicitly chosen to realign F9 with R1: **the LLM proposes follow-up
work; pages are created on demand only when the human approves.**

### 1.2 What R1 actually does (verified reference summary)

- Ingest is two-step CoT (analysis → generation). **Generation AUTO-WRITES the clearly-supported
  pages** directly to the wiki (entity/concept/schema-typed pages, the source summary with a
  guaranteed fallback, and the index/overview/log aggregates). **This auto-write stays** — even
  nashsu auto-creates obvious pages. This is exactly Synapse's current orchestrated write path
  (`write_wiki_page` + `_ensure_source_summary` + `_update_overview`) and **does not change**.
- A **separate dedicated review stage** emits review items **only for genuinely useful follow-up**.
  Review items are **PROPOSALS** and are **NOT persisted as wiki pages** until the user acts.
- **Five review item types:** `missing-page` (referenced-but-absent entity/concept → "a page to
  create"), `suggestion` (research gap/follow-up), `contradiction` (conflict with existing wiki
  content), `duplicate` (possible name collision), `confirm` (the LLM wants human confirmation).
- **Closed action set** (anti-hallucination): **Create Page / Deep Research / Skip**.
- **"Create Page" is LAZY:** it builds a skeleton (title extracted, `pageType` inferred via
  entity/concept/comparison/synthesis heuristics, target dir) and the page **content is generated
  on-demand only when the user clicks Create** — never at ingest time.
- **Auto-resolution sweep:** after each ingest-queue drain, stale review items are auto-closed —
  first rule-based (the proposed title now exists as a page), then a **conservative bounded LLM
  judgment** ("only resolve if confident the concern no longer applies; default to keeping
  pending"). `missing-page`/`duplicate` auto-resolve as the wiki grows; `contradiction`/
  `suggestion`/`confirm` are preserved for the human.
- **Anti-spam:** the review stage only runs when generation is **substantial** and only for
  "genuinely useful follow-up".

### 1.3 Ground truth this ADR builds on

- **`run_ingest_pipeline`** (`ingest/orchestrator.py`) — orchestrated branch writes pages via
  `write_wiki_page`, then calls the F9 post-write hook `_enqueue_review_items`. **This hook is the
  seam being replaced** (it is the current per-page question-spam producer).
- **`write_wiki_page(session, page, origin_source)`** — the SINGLE incremental wiki-write seam
  (I1): `persist_metadata → upsert_vector → append_log → bump_version → parse/persist wikilinks →
  update_index`. **The Create action reuses this verbatim.** One call = one `data_version` bump.
- **`resolve_provider_config("ingest", vault_id)`** (`provider_config_service.py`) + `resolve_provider`
  (`ingest/provider`) — the backend-neutral resolution path (I6). All three new LLM call sites
  (proposal emission, on-demand Create generation, the LLM sweep) route through it.
- **`run_orchestrated_loop`** (`ingest/loop.py`) — the bounded `analyze → generate → validate →
  retry` loop. The on-demand Create reuses this same loop with a single-page-target prompt
  (it does **not** invent a new generation path).
- **`Analysis` / `SuggestedPage` / `WikiPage` / `PageType`** (`ingest/schemas.py`) — the locked
  contract DTOs. `PageType` ∈ {entity, concept, source, synthesis, comparison}; `_TYPE_DIR` maps
  each to its `wiki/` subdir. Proposal `proposed_page_type` reuses these exact values.
- **`ReviewItem`** model + Alembic **0010** (current schema) — the table being migrated.
- **`/review/queue` routes** (`main.py`) — `GET`, `approve`, `skip`, `deep-research`. The
  `approve` route changes semantics; the others keep their contract.
- **The post-ingest delegated (CLI) branch** still does not enumerate pages (ADR-0025 §3.3.4) —
  the proposal stage attaches to the **orchestrated** branch only; delegated remains a reserved
  follow-up (§9 risk 1, unchanged from ADR-0025).

---

## 2. Decision summary

1. **Auto-create stays.** The orchestrated generation step keeps auto-writing the clearly-supported
   pages (`write_wiki_page` for each `WikiPage`, the `_ensure_source_summary` fallback, and the
   overview/index/log aggregates). I1 is unchanged. This ADR does **not** touch the generation
   contract or the write seam.
2. **Replace `generate_review_queries` (per-page question-spam) with a single bounded `propose_reviews`
   stage** that runs **once per ingest run** (not once per page), gated by an **anti-spam heuristic**,
   and emits **0..N PROPOSALS** of five types: `missing-page | suggestion | contradiction |
   duplicate | confirm`. Proposals are **not** wiki pages.
3. **Redesign `review_items`** (new Alembic migration **0013**; 0011/0012 are taken — see §3):
   rename/repurpose `item_type` to the five-value enum; **add** `proposed_title`,
   `proposed_page_type`, `proposed_dir`, `rationale`, `source_page_id`, `resolution`; **drop**
   `pre_generated_query` (questions become `rationale` text or a `suggestion` proposal). `page_id`
   stays as the **review target** for contradiction/duplicate items (the existing page in conflict);
   `created_page_id` is added to record the page a Create produced.
4. **Create action = lazy on-demand generation.** `POST /review/queue/{id}/approve` becomes
   **Create**: it derives a skeleton from the proposal, runs the **bounded** orchestrated loop
   (I6/I7) targeting that single page, writes it through `write_wiki_page` (I1 — one `data_version`
   bump), sets `status=created` + `created_page_id`, and returns the created `ReviewItem`. Content
   is generated **only here**, never at ingest time.
5. **Auto-resolution sweep** runs after each ingest-queue drain: a **rule-based pass** (proposed
   title now exists → resolve `missing-page`/`duplicate`) then a **conservative bounded LLM pass**
   (I6/I7; default = keep pending). `contradiction`/`suggestion`/`confirm` are **never**
   auto-resolved by rule; the LLM pass may resolve them only with high confidence.
6. **REST:** `approve` → Create semantics (now possibly **201/202** because it does real work);
   `skip` and `deep-research` keep their ADR-0025 contract. A new optional
   `POST /review/queue/sweep` exposes the sweep for manual trigger / testing; it is also wired to
   fire after the orchestrated ingest run completes.
7. **All three new LLM call sites are bounded (I7)** by the resolved provider row's `max_iter` +
   `token_budget`, wrapped in a timeout, and log `total_cost_usd`. Failure degrades safely (no
   proposals / no Create / keep pending) and never fails the ingest.

---

## 3. New `review_items` data model

New Alembic migration **0013** (0010 = original `review_items`; 0011 = `remote_mcp_enabled`;
0012 = `mcp_access_token_hash`/`mcp_allow_without_token` — all taken). The `ReviewItem` model in
`backend/app/models.py` is rewritten; `make er` MUST regenerate `docs/er/schema.mmd` and
`make openapi` MUST regenerate `docs/api/openapi.json` (I8).

### 3.1 Column changes

| Column | Action | Type / nullability | Notes |
|--------|--------|--------------------|-------|
| `id` | keep | UUID PK | `UUID(as_uuid=True).with_variant(String(36),"sqlite")` (unchanged). |
| `vault_id` | keep | String, not null | Existing String identifier; no `vaults` table (AQ-v0.5-6). |
| `item_type` | **repurpose** | Text, not null | **New enum-by-convention:** `missing-page \| suggestion \| contradiction \| duplicate \| confirm`. Old values (`new_page`/`update_page`/`deep_research_candidate`) are removed. App-level `Literal` validation (house convention; no DB CHECK — consistent with ADR-0025 §3.1 amendment). |
| `status` | **extend** | Text, not null, default `pending` | New lifecycle: `pending \| created \| skipped \| deep_researched \| auto_resolved`. (`approved` is gone; Create produces `created`.) |
| `page_id` | **keep, re-document** | UUID FK → `pages.id`, nullable | Now the **review TARGET**: the existing page a `contradiction`/`duplicate` conflicts with, or the source-context page for a `missing-page`/`suggestion`. NULL when none applies. |
| `source_page_id` | **add** | UUID FK → `pages.id`, nullable | The page **whose ingest produced this proposal** (provenance / “came from”). Distinct from `page_id` (the conflicting/target page). Lets the UI show "proposed while ingesting X". |
| `proposed_title` | **add** | Text, nullable | The title the LLM proposes to create (required for `missing-page`; advisory for others). Drives the skeleton + the rule-based sweep match. |
| `proposed_page_type` | **add** | Text, nullable | Inferred `PageType` value (`entity\|concept\|source\|synthesis\|comparison`) for the lazy skeleton. NULL → resolved at Create time by heuristic (§5.2). |
| `proposed_dir` | **add** | Text, nullable | Target `wiki/` subdir derived from `proposed_page_type` via `type_subdir` (`entities`/`concepts`/`sources`/`synthesis`/`comparisons`). Stored for UI transparency; recomputed at Create from the final type (never trusted blindly — see §5.2). |
| `rationale` | **add** | Text, nullable | Short human-readable "why this matters" (replaces the old follow-up questions). For `suggestion` this is the gap/follow-up; for `contradiction` the conflict description; for `confirm` what needs confirming. |
| `resolution` | **add** | Text, nullable | Label set when the row leaves `pending`: `created \| skipped \| researched \| rule_resolved \| llm_resolved`. NULL while pending. Audit of *how* the item closed (complements `status`). |
| `created_page_id` | **add** | UUID FK → `pages.id`, nullable | The page a successful **Create** produced. NULL otherwise. Distinct from `page_id`/`source_page_id`. |
| `deep_research_run_id` | **keep** | UUID FK → `deep_research_runs.id`, nullable | Set by the Deep-Research action (AC-F10-5). Unchanged. |
| `pre_generated_query` | **DROP** | — | Superseded by `rationale` (+ the `suggestion` type for explicit research gaps). The Deep-Research action now derives its topic from `proposed_title`/`rationale`/`page.title`. |
| `created_at` | keep | TIMESTAMP(tz), not null | Unchanged. |
| `reviewed_at` | keep | TIMESTAMP(tz), nullable | Set on any terminal action (incl. auto-resolve). |
| `reviewed_by` | keep | Text, nullable | `"web-ui"` for human actions; `"auto-sweep"` for the sweep. |

**Index:** keep `ix_review_items_vault_status_created` on `(vault_id, status, created_at)` — the
paginated pending-queue read is unchanged. **Add** a non-unique index
`ix_review_items_vault_proposed_title` on `(vault_id, proposed_title)` to make the rule-based sweep
("does a page with this title now exist?") and duplicate-collision lookups cheap (bounded read,
no scan).

### 3.2 Migration 0013 notes

- 0013 is an **online-safe** additive-plus-rename change: ADD the six new columns
  (`source_page_id`, `proposed_title`, `proposed_page_type`, `proposed_dir`, `rationale`,
  `resolution`, `created_page_id`) all nullable; DROP `pre_generated_query`. `item_type`/`status`
  stay `Text` so **no type alteration** is required — only the *accepted value set* changes
  (enforced app-side).
- **Pre-existing rows:** M5 is single-operator pre-release; there is no production data to
  migrate. The migration MAY left-shift any legacy `new_page`/`approved` rows to `skipped` +
  `resolution='skipped'` (they referenced auto-created pages that already exist, so they are
  obsolete under the new model). This is a one-line data step in the migration; document it.
- **`make er` (I8):** regenerate `docs/er/schema.mmd` — the `review_items` entity gains three FKs
  to `pages` (`page_id`, `source_page_id`, `created_page_id`) plus the FK to `deep_research_runs`.
  Zero-drift gate.

---

## 4. Proposal emission logic (replaces `generate_review_queries`)

### 4.1 Where it runs

A new module function `propose_reviews(...)` in `ops/review.py` runs **once per orchestrated ingest
run**, from `run_ingest_pipeline` **after** the pages are written and `_update_overview` completes —
the same fire-and-forget seam the old `_enqueue_review_items` occupied. It **replaces**
`_enqueue_review_items` + `generate_review_queries` entirely.

Inputs available at that point (no extra retrieval needed): the run's `Analysis` (topics, entities,
`suggested_pages`, summary), the list of `WikiPage`s actually written (and their persisted `Page`
rows), the `origin_source`, and `vault_id`. The stage MAY also read the `links` table for the run's
pages to detect **dangling wikilinks** (referenced-but-absent targets → `missing-page` candidates) —
this is a bounded indexed read of the just-written pages' links (I1; no vault re-scan, no FA2).

### 4.2 Anti-spam gate (runs BEFORE any LLM call)

Mirroring R1's "only when generation is substantial OR explicit follow-up exists", the stage runs
the (cost-bearing) LLM proposal call **only if** at least one of:

- total written content ≥ `REVIEW_PROPOSE_MIN_CHARS` (default ~10_000), **OR**
- number of pages written ≥ `REVIEW_PROPOSE_MIN_PAGES` (default 4), **OR**
- there is at least one **dangling wikilink** among the written pages (a concrete `missing-page`
  signal), **OR**
- the `Analysis.suggested_pages` contains a page that was **not** written (the analysis proposed it
  but generation skipped it — a concrete `missing-page`/`suggestion` signal).

The dangling-link and not-written-suggested-page checks are **rule-based** and may **directly emit**
`missing-page`/`duplicate` proposals **without an LLM call** (cheapest path; deterministic). The LLM
call is reserved for `suggestion`/`contradiction`/`confirm` judgment and runs only if the gate
above passes. If the gate fails, the stage emits **zero** proposals (no spam, no cost).

### 4.3 The single bounded proposal call (I6/I7)

When the gate passes, the stage makes **at most one** `InferenceProvider` call (operation
`"ingest"`, resolved via `resolve_provider_config` — I6) that, given the analysis + a compact
digest of the written pages + the list of existing page titles in the vault (a bounded title list,
not full content), returns a **structured list of proposals** (JSON: `type`, `proposed_title`,
`proposed_page_type`, `rationale`, optional `target_page_title` for contradiction/duplicate).

Bounds (I7):

- **One call, no loop, no retry** for the proposal stage (the *Create* action separately uses the
  bounded loop — §5). The call is wrapped in `asyncio.wait_for(REVIEW_PROPOSE_TIMEOUT_SECONDS)`.
- Output capped by the resolved row's `token_budget` (or `REVIEW_PROPOSE_TOKEN_BUDGET` default).
- **Proposal count cap** `REVIEW_PROPOSE_MAX_ITEMS` (default 8) — truncate; never enqueue an
  unbounded list.
- Cost pushed through `UsageAccumulator` and logged (`total_cost_usd`), same as every provider call.
- **Failure (timeout / no provider / parse error) → emit only the rule-based proposals (if any),
  log a WARNING, never fail the ingest** (the pages are already written; the queue is advisory).

Each returned proposal becomes one `enqueue_review(...)` row (pure DB write) with
`status=pending`, `item_type=<type>`, `proposed_title`, inferred `proposed_page_type` +
`proposed_dir`, `rationale`, `source_page_id` (the run's primary written page or NULL),
and — for `contradiction`/`duplicate` — `page_id` resolved to the conflicting existing page
(looked up by `target_page_title`, bounded indexed read).

### 4.4 Why once-per-run, not once-per-page

The old model's per-page call was both the I7 surface and the spam source. Once-per-run with an
anti-spam gate: (a) collapses N calls to ≤1, lowering cost and latency; (b) lets the model reason
across the whole batch (cross-page contradictions/duplicates are only visible run-wide); (c) matches
R1. This is a strict improvement on the ADR-0025 I7 surface.

---

## 5. The Create action (lazy on-demand generation)

`POST /review/queue/{id}/approve` is **repurposed to Create** (path kept for UI stability; see §6.1
for the optional alias). It is the **only** place a proposal becomes a wiki page, and the **only**
new on-demand generation path.

### 5.1 Flow

```
1. Load the review item (404 if absent; 409 if status != 'pending').
2. Derive the skeleton (§5.2): final title, final PageType, target dir.
3. Resolve the ingest provider (resolve_provider_config("ingest", vault_id)) — I6.
   If none configured → 409 "no ingest provider configured" (never hardcode — I6).
4. Run the BOUNDED orchestrated loop (run_orchestrated_loop, ingest/loop.py) with a
   single-page-target prompt: "generate the wiki page titled <title> of type <type>,
   grounded in the vault context + the proposal rationale". max_iter + token_budget from
   the resolved row (I7). Wrapped in a timeout; cost logged to an ingest_runs row
   (route='orchestrated', the existing audit path).
5. Take the produced WikiPage (or the _ensure_source_summary fallback if the loop produced
   none) and write it through write_wiki_page(None, page, origin_source) — the SAME
   incremental seam (I1): persist_metadata → upsert_vector → append_log → bump_version
   → wikilinks → update_index. This bumps data_version exactly ONCE.
6. Set status='created', resolution='created', created_page_id=<new page id>,
   reviewed_at=now(), reviewed_by='web-ui'. Return the updated ReviewItem.
7. (Optional, fire-and-forget) trigger the auto-resolution sweep (§6) so sibling
   missing-page/duplicate proposals that this new page satisfies are closed.
```

`origin_source` for the created page is the proposal's provenance: the `source_page_id`'s
`file_path` if set, else a synthetic `review:<item_id>` marker so F3 traceability (`sources[]`
non-empty) holds.

### 5.2 Deriving the skeleton (title / type / dir)

- **Title:** `proposed_title` (set at emission). For a `missing-page` from a dangling wikilink, this
  is the wikilink target text. Slugged by the existing `_slugify` at write time.
- **Type:** prefer the stored `proposed_page_type`. When NULL, infer with a conservative heuristic
  over title + rationale, mapped to the existing `PageType` enum (no new types):
  - comparison cues ("vs", "versus", "compared", "comparison") → `comparison`
  - synthesis cues ("overview of", "summary of", "survey", "landscape") → `synthesis`
  - proper-noun / named-entity shape (capitalized multi-word, person/org/product) → `entity`
  - default → `concept`
  (`source` is reserved for ingested raw documents and is **not** a Create target — Create never
  fabricates a source page.)
- **Dir:** always `type_subdir(final_type)` recomputed from the **final** resolved type at write
  time — never trust a stale `proposed_dir` (it is UI-display only). This keeps the file in the
  correct `wiki/` subdir (I5).

### 5.3 Bounds and failure (I7)

- The Create loop is the bounded `run_orchestrated_loop` — `max_iter` + `token_budget` from the
  resolved row; an `ingest_runs` row records tokens + `total_cost_usd` + the `$1` anomaly check
  (reusing the existing finalize path). One Create = one bounded run.
- On loop failure / provider error: the item stays `pending` (NOT consumed), the endpoint returns
  **502** (`"page generation failed; item left pending — retry or skip"`), and **no partial page**
  is written (write only happens on a produced/fallback `WikiPage`). No silent half-create.
- On "no ingest provider configured": **409**, item stays pending (I6 — never substitute a backend).

---

## 6. Auto-resolution sweep

A new function `sweep_reviews(vault_id)` in `ops/review.py`.

### 6.1 Trigger points

1. **After each orchestrated ingest run drains** (fire-and-forget from `run_ingest_pipeline`, after
   `propose_reviews`). As the wiki grows, previously-proposed `missing-page`/`duplicate` items may
   now be satisfied.
2. **After a successful Create** (§5.1 step 7) — closes sibling proposals the new page satisfies.
3. **Manual:** `POST /review/queue/sweep` (testing / explicit user "clean up").

The sweep is **fire-and-forget and never fails ingest** (try/except; log WARNING).

### 6.2 Pass 1 — rule-based (no LLM, deterministic, I1)

For each `pending` `missing-page` or `duplicate` item in the vault (bounded read, ordered, capped at
`REVIEW_SWEEP_MAX_ITEMS` per sweep — I7):

- Look up whether a **live** `pages` row now exists whose title matches `proposed_title`
  (case/whitespace-normalized; bounded indexed read via `ix_review_items_vault_proposed_title` +
  a `pages` title lookup — **no vault re-scan**, I1).
- If yes → set `status=auto_resolved`, `resolution='rule_resolved'`, `reviewed_at=now()`,
  `reviewed_by='auto-sweep'`, `page_id`/`created_page_id` left as recorded.

`contradiction`, `suggestion`, `confirm` are **never** touched by Pass 1 — they require human or
high-confidence-LLM judgment.

### 6.3 Pass 2 — conservative bounded LLM (I6/I7)

Optional and gated by `REVIEW_SWEEP_LLM_ENABLED` (default on, but a single bounded call). For the
remaining `pending` items **not** resolved by Pass 1, the sweep MAY make **at most one** bounded
`InferenceProvider` call (operation `"ingest"`, resolved via `resolve_provider_config` — I6) that,
given each item's `rationale`/`proposed_title` + the current vault title list + the conflicting
page's content digest (for contradictions), returns a per-item verdict: **resolve** or **keep**.

Bounds + safety (I7):

- **One call per sweep**, batching the candidate items (capped at `REVIEW_SWEEP_LLM_MAX_ITEMS`,
  default 8); no loop; `asyncio.wait_for(REVIEW_SWEEP_TIMEOUT_SECONDS)`; `token_budget` from the
  resolved row; cost logged.
- **Default-to-keep:** the prompt instructs "only resolve if you are confident the concern no
  longer applies; otherwise keep pending." Any parse ambiguity, timeout, or provider error → **keep
  all pending** (never auto-close on uncertainty). This is the conservative bias R1 requires.
- Items resolved by Pass 2 → `status=auto_resolved`, `resolution='llm_resolved'`,
  `reviewed_by='auto-sweep'`.
- `confirm` items are **never** auto-resolved (human confirmation is the whole point); `suggestion`
  and `contradiction` MAY be resolved by Pass 2 only with high confidence.

### 6.4 What auto-resolves vs is preserved

| Type | Pass 1 (rule) | Pass 2 (LLM, conservative) | Preserved for human |
|------|---------------|----------------------------|---------------------|
| `missing-page` | resolves if title now exists | may resolve | otherwise |
| `duplicate` | resolves if collision gone | may resolve | otherwise |
| `suggestion` | never | may resolve (high confidence) | default |
| `contradiction` | never | may resolve (high confidence) | default |
| `confirm` | never | **never** | always |

---

## 7. REST API changes (D4)

Base path `/review/queue` unchanged. `make openapi` regenerates `docs/api/openapi.json` (I8).

| Method + path | Body / params | Success | Notes |
|---------------|---------------|---------|-------|
| `GET /review/queue` | `?vault_id&limit&offset` (limit default 50, max 200 — I7) | 200 `{items:[…], total, limit, offset}` | Items now carry the new projection (§7.1). Unchanged paging. |
| `POST /review/queue/{id}/approve` | — | **201** `ReviewItem` (page created) | **Now = Create (§5).** Runs the bounded loop, writes the page (I1), bumps `data_version` once, sets `status=created` + `created_page_id`. 409 if item not `pending` or no ingest provider; 502 if generation fails (item left pending). |
| `POST /review/queue/{id}/create` | — | **201** `ReviewItem` | **Optional explicit alias** for `approve`, recommended so the UI verb matches the action. Both routes call the same handler. (`approve` retained only for backward path stability.) |
| `POST /review/queue/{id}/skip` | — | 200 `ReviewItem` | `status=skipped`, `resolution='skipped'`. Unchanged. |
| `POST /review/queue/{id}/deep-research` | — | 202 `{review_item_id, run_id}` | `status=deep_researched`, `resolution='researched'`. Topic now derived from `proposed_title` → `rationale` (first line) → `page.title` (was `pre_generated_query`). 503 if `SEARXNG_URL` unset. |
| `POST /review/queue/sweep` | `?vault_id` | 200 `{rule_resolved, llm_resolved, kept}` | Manual auto-resolution sweep (§6). Bounded; idempotent. |
| (all action routes) | unknown `id` | 404 | |

### 7.1 `ReviewItem` JSON projection

```
{
  id, vault_id,
  item_type,            // missing-page | suggestion | contradiction | duplicate | confirm
  status,               // pending | created | skipped | deep_researched | auto_resolved
  proposed_title,       // string | null
  proposed_page_type,   // entity|concept|source|synthesis|comparison | null
  proposed_dir,         // string | null (display only)
  rationale,            // string | null
  page_id,              // target/conflicting existing page | null
  page_title?,          // convenience join from pages for page_id (UI)
  source_page_id,       // provenance page | null
  created_page_id,      // page produced by Create | null
  resolution,           // created|skipped|researched|rule_resolved|llm_resolved | null
  deep_research_run_id, // uuid | null
  created_at, reviewed_at
}
```

The frontend **Review** section renders item type + `proposed_title` + `rationale`, and the
**Create / Deep Research / Skip** buttons (the closed action set). List is virtualized > 50 (I4).
After Create, the UI should refresh on the bumped `data_version` (F16) to reflect the new page.

---

## 8. Invariant compliance

| Inv | How this design guarantees it |
|-----|-------------------------------|
| **K8** | The LLM now **proposes** follow-up work; the human **decides** (Create / Deep-Research / Skip). Pages are created only on explicit human approval. The inversion in ADR-0025 (human rubber-stamps auto-created pages) is corrected — this is the headline. Auto-create of *clearly-supported* pages stays, exactly as R1 does. |
| **I1** | Auto-create write path is unchanged (`write_wiki_page`, incremental). The **Create** action writes through the same single seam → one `data_version` bump, no re-scan. The **sweep** uses bounded indexed reads (title lookups) — never a full vault re-scan and never FA2 (I2 untouched: the proposal stage reads the `links` table, not the graph layout). |
| **I6** | All three new LLM call sites (proposal emission §4.3, on-demand Create §5, LLM sweep §6.3) resolve via `resolve_provider_config(...)` + `resolve_provider(...)`; routing is capability-agnostic text/JSON in/out. No `isinstance`/`provider_type`/class-name branching, no hardcoded backend or model. "No ingest provider configured" is a 409, never a silent default (I6 hard rule). |
| **I7** | Proposal stage: anti-spam gate, **≤1** provider call, `REVIEW_PROPOSE_MAX_ITEMS` cap, timeout, token_budget, cost logged. Create: the **bounded** `run_orchestrated_loop` (`max_iter`+`token_budget`), one `ingest_runs` row with `total_cost_usd` + `$1` anomaly check. Sweep: Pass-1 capped at `REVIEW_SWEEP_MAX_ITEMS` (no LLM), Pass-2 **≤1** call batched + capped + timeout + default-to-keep. No unbounded loop anywhere. |
| **I8** | `review_items` schema change → Alembic **0013** + `make er` regenerates `docs/er/schema.mmd` (three FKs to `pages` + the `deep_research_runs` FK) + `make openapi` regenerates the changed/added `/review/queue` endpoints. D3 sequence diagram for the ingest loop SHOULD gain the propose/Create/sweep steps (tech-writer). No sprint-done without these artifacts consistent. |

No invariant is traded. The two genuine tensions are flagged in §9.

---

## 9. Flagged tensions & risks

1. **Delegated (CLI) ingest still emits no proposals.** As in ADR-0025 §3.3.4, the orchestrator
   does not enumerate pages the CLI agent writes via MCP `write_page`, so `propose_reviews` attaches
   to the **orchestrated** branch only. **Not a violation** — it is an explicit, recorded gap. A
   future option is to have the MCP `write_page` tool record written titles so a post-delegation
   sweep can run; that needs its own ADR.
2. **Create cost is now interactive.** Each Create is a full bounded ingest run (latency + API cost),
   not a no-op. This is the intended behavior (lazy generation), bounded by `max_iter`/`token_budget`
   and surfaced in `ingest_runs`. The UI must show a spinner and the cost is logged like any run.
3. **Proposal/sweep LLM calls add cost per ingest.** Bounded to ≤1 call each, gated by the anti-spam
   heuristic (proposal) and the rule-based-first ordering (sweep). For zero-cost operation, set
   `REVIEW_SWEEP_LLM_ENABLED=false` (Pass 1 still runs) and rely on the substantial-generation gate.
4. **Type-inference heuristic (§5.2) can mis-type a Create.** Conservative defaults (`concept`) and
   the fact that the human chose to Create (and can fix frontmatter in CodeMirror, I4) bound the
   blast radius. The heuristic never invents a `source` page.
5. **Auto-resolution false-positives.** Mitigated by Pass-1 exact-title match only and Pass-2's
   default-to-keep + never-resolve-`confirm` rule. A wrongly-resolved item is recoverable (the page
   it pointed to still exists or the human re-proposes); we bias hard toward keeping pending.
6. **`approve`→Create semantics change is breaking** for any client expecting a no-op. M5 is
   single-operator pre-release; the new `/create` alias (§7) is the forward verb and `approve` is
   kept only for path stability. Documented in USER.md (tech-writer).

---

## 10. Do-NOT list (rejection triggers)

A PR touching this redesign is **rejected on review** if it:

1. **DO NOT** auto-create a page from a proposal at ingest time. Proposals are never wiki pages until
   Create (§5). (Auto-create of *clearly-supported* generation output stays — that is a different,
   unchanged path.)
2. **DO NOT** make Create (or any action) write to `wiki/` by any path other than `write_wiki_page`
   (I1/I5). One Create = one `data_version` bump.
3. **DO NOT** make more than one provider call in the proposal stage, more than one batched call in
   the sweep LLM pass, or run the Create generation outside the bounded `run_orchestrated_loop` (I7).
4. **DO NOT** hardcode a backend/model anywhere — resolve via `resolve_provider_config("ingest")`;
   "no provider" is a 409/skip, never a silent default (I6).
5. **DO NOT** let `propose_reviews` or `sweep_reviews` raise into the ingest critical path
   (fire-and-forget; try/except; the page is already written).
6. **DO NOT** re-scan the vault or touch FA2 in the sweep — bounded indexed reads only (I1/I2).
7. **DO NOT** auto-resolve `confirm` items, or auto-resolve any item on parse ambiguity / timeout
   (default-to-keep, §6.3).
8. **DO NOT** ship the schema change without Alembic 0013 + `make er` + `make openapi` regenerated
   (I8).
9. **DO NOT** return an unbounded `GET /review/queue` page (limit capped 50/max 200) or an unbounded
   proposal/sweep list (caps in §4.3/§6).
10. **DO NOT** skip TanStack virtualization for the review list when it can exceed 50 rows (I4).

---

## 11. Implementation plan

> **Routing rule for the orchestrator:** any item below tagged **[AI]** touches `InferenceProvider`
> (proposal emission, on-demand Create generation, or the sweep LLM pass) and MUST be routed to
> **ai-agent-engineer** (CLAUDE.md §13). Items tagged **[BE]** are backend-engineer. Both must land
> behind green tests; QA + tech-writer gate as usual.

### 11.1 backend-engineer [BE]

- **Model + migration:** rewrite `ReviewItem` in `models.py` per §3.1; write Alembic **0013**
  (add columns, drop `pre_generated_query`, the legacy-row left-shift step, the new index). Update
  the model docstring. Run `make er` → commit `docs/er/schema.mmd` (I8).
- **REST surface:** update the four existing handlers in `main.py` + the `ReviewItemResponse`
  Pydantic model to the §7.1 projection; change `approve` to call the new Create handler (return
  201); add the `/create` alias and `POST /review/queue/sweep`. Update `deep_research`'s topic
  derivation to use `proposed_title`/`rationale` instead of `pre_generated_query`. Run `make openapi`
  → commit `docs/api/openapi.json` (I8).
- **`enqueue_review` rewrite:** accept the new fields (`item_type` enum, `proposed_*`, `rationale`,
  `source_page_id`, `page_id`); pure DB write (no provider — unchanged contract).
- **Sweep Pass 1 (rule-based)** in `ops/review.py`: `sweep_reviews` skeleton + the deterministic
  title-match resolution (bounded read, caps). Wire the fire-and-forget triggers in
  `run_ingest_pipeline` (after `propose_reviews`) and after a successful Create. **No LLM here.**
- **Dangling-link / not-written-suggested-page detection** (the rule-based proposal inputs in §4.2)
  — bounded `links`/`pages` reads; emits `missing-page`/`duplicate` rows directly (no provider).
- **Queue list / status lifecycle helpers:** `_set_status` extended for the new statuses +
  `resolution` label; `list_queue` projection unchanged structurally.
- **Frontend (frontend-engineer, coordinated by BE):** Review section renders the new projection +
  Create/Deep-Research/Skip; virtualize > 50 (I4); refresh on `data_version` after Create.
- **Tests:** migration up/down; `approve`→Create writes exactly one page + one `data_version` bump;
  409 when no provider / not pending; 502 leaves item pending; sweep Pass-1 resolves
  `missing-page`/`duplicate` on title match and never touches `confirm`; `GET` projection; caps.

### 11.2 ai-agent-engineer [AI]

- **[AI] `propose_reviews`** (§4.3): the single bounded `InferenceProvider` call (resolve via
  `resolve_provider_config("ingest")`, I6), the structured-proposal prompt + JSON parse into the
  five types, the anti-spam gate (§4.2), the `REVIEW_PROPOSE_*` bounds + timeout + cost logging
  (I7), and the fire-and-forget failure degradation (rule-based-only / none). **Replaces**
  `generate_review_queries` + `_enqueue_review_items`.
- **[AI] On-demand Create generation** (§5): the single-page-target prompt + invocation of the
  bounded `run_orchestrated_loop` for one Create, the `_ensure_source_summary` fallback wiring, the
  `ingest_runs` cost row + `$1` anomaly reuse, the 502-on-failure (item left pending) behavior.
  The skeleton type/dir heuristic (§5.2) is shared with BE but the **generation** is [AI].
- **[AI] Sweep Pass 2 (LLM)** (§6.3): the single batched bounded call (resolve via
  `resolve_provider_config`, I6), the conservative default-to-keep prompt, `REVIEW_SWEEP_LLM_*`
  bounds + timeout + cost logging (I7), never-resolve-`confirm` rule, and keep-all-on-uncertainty.
- **[AI] Bounds config:** add `REVIEW_PROPOSE_MIN_CHARS/MIN_PAGES/MAX_ITEMS/TOKEN_BUDGET/TIMEOUT`,
  `REVIEW_SWEEP_MAX_ITEMS`, `REVIEW_SWEEP_LLM_ENABLED/MAX_ITEMS/TOKEN_BUDGET/TIMEOUT` to `config.py`
  with the §4/§6 defaults; document in DEPLOY.md (tech-writer).
- **[AI] Tests:** anti-spam gate skips the call on a trivial run (zero proposals, zero cost);
  proposal call bounded to one + capped + timeout-degrades; Create runs the bounded loop and writes
  through `write_wiki_page`; sweep Pass-2 keeps on ambiguity and never resolves `confirm`.

### 11.3 Cross-cutting (handoffs)

- **tech-writer:** this ADR (format + README row), USER.md (new Create/Skip/Deep-Research verbs +
  the proposal model), DEPLOY.md (new env vars), and the D3 ingest sequence diagram (add
  propose → Create → sweep). C4: **no new container/component** — `ops/review.py` stays a component
  inside the FastAPI service; no D1 topology change.
- **qa-test-engineer:** the §10 Do-NOT list is the rejection gate; the §11 tests are the milestone
  gate.
- **orchestrator:** route [AI] items to ai-agent-engineer, [BE] items to backend-engineer; human
  checkpoint before merge (the `approve`→Create semantics change is owner-approved).

---

## 12. Sign-off

**APPROVED to implement.** The redesign keeps auto-create for clearly-supported pages (I1 unchanged),
replaces per-page question-spam with a single bounded, anti-spam-gated proposal stage emitting five
proposal types, makes `approve`→**Create** generate the page on-demand through the resolved
`InferenceProvider` and the existing incremental write seam (one `data_version` bump), and adds a
rule-then-bounded-LLM auto-resolution sweep that defaults to keeping items pending. K8 is restored:
the LLM proposes, the human curates. I1, I6, I7, I8 are satisfied; I2/I4/I5 are untouched. The
`review_items` change ships as Alembic **0013** with `make er` + `make openapi` regenerated.

> Handoff: ADR-0034 → tech-writer (format, README row, USER/DEPLOY, D3 sequence). Interface contracts
> (§3, §4, §5, §6, §7) → backend-engineer [BE] + ai-agent-engineer [AI] per §11. PR verdicts →
> orchestrator. This ADR supersedes the F9 parts of ADR-0025 (F12 parts stand).
