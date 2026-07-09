# ADR-0058 — Lint parity extension: broken-wikilink, batch actions, review bridge, orphan delete

- **Status:** Accepted
- **Date:** 2026-07-06
- **Sprint:** v1.3 UI-alignment batch **B1 — Lint** (`docs/reference/UI-ALIGNMENT-PLAN-2026-07.md`)
- **Extends:** ADR-0037 (K2 Lint-fix loop). This ADR does **not** supersede it — the two-phase
  scan (deterministic + bounded semantic), the human-gate model, and the I7 bounds table of
  ADR-0037 stand unchanged. This ADR *adds* one deterministic category, four new endpoints, two
  columns, and one page-delete verb, all within ADR-0037's safety envelope.
- **Features:** K2 (Lint — the third Karpathy operation) · K8 (human curates, LLM maintains — the
  batch bar, the review bridge, and orphan delete are all human-gated curation surfaces) ·
  F9 (the lint→review bridge feeds the existing HITL queue) · F13 (orphan delete reuses the
  cascade-delete cleanup seams) · F4 (broken-wikilink reads the `links` health signal)
- **Reference:** R1 (nashsu/llm_wiki — `lint-view.tsx` / `lint-fixes.ts`: the live lint screen with
  issue-count badge, `Semantic (LLM)` checkbox, severity group headers, batch bar, green
  `Suggested target:` strip, per-row `Open`/`Fix`, `Delete` on orphans) · R2 (Karpathy — periodic
  lint/health-check)
- **Invariants owned:** **I1** (broken-wikilink is a bounded indexed scan of `links.dangling=True`
  rows — never a vault walk; every apply/delete touches only the affected page(s) and bumps
  `data_version` **at most once**) · **I5** (the broken-wikilink rewrite and the orphan-delete
  dead-link cleanup edit page **bodies** only, via frontmatter-preserving round-trip) · **I6** (the
  `?semantic=false` path skips the provider entirely; when `true`, the semantic pass still routes
  through the ADR-0037 backend-neutral surface — no hardcoded backend, no `isinstance` branching) ·
  **I7** (broken-wikilink is **zero provider cost** and scan-capped; `/lint/findings/batch` caps
  `ids ≤ 200` and runs sequentially server-side; no new unbounded loop) · **I4** (severity group
  headers are synthetic rows inside the existing TanStack-virtualised list — no de-virtualisation) ·
  **K8** (`DELETE /pages/{id}` is **UI-only, human-double-confirmed**, never reachable from any
  automated loop; every fix stays propose-then-apply)
- **Author:** solution-architect

---

## 1. Context

The live nashsu/llm_wiki lint screen is the batch's headline gap (B1, P0). Its lint view shows a
health surface Synapse's backend already *computes* but never *exposes*: an issue-count badge, a
`Semantic (LLM)` toggle, severity group headers (`⚠ Warnings (741)`), a multi-select **batch bar**
(`Select all / Fix selected / Send selected to Review / Ignore selected`), a per-row `Open`/`Fix`
pair, a green `Suggested target:` strip, and a `Delete` action on orphan rows. The "741 warnings"
that dominate the reference screen are, structurally, **broken wikilinks** — which Synapse already
tracks (`links.dangling=True`, `backend/app/wiki/links.py`) but never surfaces as findings.

ADR-0037 shipped the K2 lint **vertical** (deterministic `orphan-page` + a bounded semantic pass
over `missing-xref` / `contradiction` / `stale-claim` / `missing-page`, plus `apply`/`dismiss`
human gates). The B1 batch closes the *UI-affordance* gap on top of that vertical without loosening
any of its guarantees. The controlling tensions are unchanged from ADR-0037:

- **K8** — a lint screen that batch-mutates a human-curated vault on one click is exactly the loss
  of control K8 forbids. So every new action is either a *bounded, reversible-by-the-human* body
  edit (the same safety class ADR-0037 §5 already permits for `missing-xref`), a *proposal handoff*
  (the review bridge), or a *human-double-confirmed destructive* verb (orphan delete). Nothing
  auto-runs.
- **I7** — the new "741 warnings" category must not add provider cost. It is fully deterministic.
- **I1/I5** — every edit stays a single-page, body-only, one-bump operation. No rescans.

The implementation contract is already pinned (backend + frontend engineers are building it in
parallel); this ADR *documents and gates* that contract against the invariants.

## 2. Decision

Extend the K2 lint module (`backend/app/ops/lint.py`) and its persistence with the following. Each
item names the ADR-0037 seam it reuses so the vertical stays *one* idiom, not a fork.

### 2.1 New deterministic category `broken-wikilink` (I1, I7, zero cost)

Add `broken-wikilink` to `_VALID_CATEGORIES`. It is produced by the **deterministic** phase of
`run_lint_scan` (alongside `orphan-page`), so it costs **nothing** and runs even when no provider is
configured. It reads only the `links` table — a **bounded** indexed scan of `links.dangling=True`
rows (capped at `_BROKEN_SCAN_MAX_LINKS = 1000`, its **own** I7 bound — see §2.1a); **never** a
vault walk (I1).

**Finding semantics (deliberate, and different from the graph-signal categories):**

| Field | Value for `broken-wikilink` |
|-------|-----------------------------|
| `target_page_id` | the **referencing** page — the page whose body contains the broken `[[link]]`. (This is what the row's `Open` button navigates to: you fix a broken link by opening the page that has it.) |
| `target_title` | the **dangling target text** — the literal `[[Target]]` string that failed to resolve. |
| `suggested_target` (new col) | the tolerant-resolver best-guess title, computed **at scan time** via the exact → case-insensitive → slug precedence (the same family as `reresolve_dangling_links` / `_resolve_target` in `backend/app/wiki/links.py`). `NULL` when nothing plausibly matches. |
| `suggested_page_id` (new col) | the live page id backing `suggested_target`, or `NULL`. |
| `severity` | `warning` (mirrors the llm_wiki "Warnings" grouping). |

**Dedup:** a new broken-wikilink finding is suppressed when an identical **open** finding already
exists for the same `(vault_id, target_page_id, target_title)` — so re-scanning does not pile up
duplicate rows for the same unresolved link. (Same spirit as the ADR-0037 semantic-pass dedup and
the review-queue FNV-1a content-key: a finding is a *sighting*, and a still-open sighting is not
re-emitted.)

### 2.1a Cap separation: `LINT_MAX_FINDINGS` bounds the PAID pass only (amended 2026-07-06, live-preview)

**Defect found in live-preview verification** (986-page vault, 155 dangling links): the original
`run_lint_scan` merged deterministic + semantic findings into one list and truncated the total at
`LINT_MAX_FINDINGS` (default 50). Because orphan-page detection ran first and a real vault has ≥50
orphans, the cap was exhausted by orphans alone and **every broken-wikilink finding was truncated to
zero** — the single most important llm_wiki-parity category (its lint page is dominated by "Broken
Link" warnings) never reached the UI. Unit tests missed it: their fixtures had < 50 findings.

**Root cause:** `LINT_MAX_FINDINGS` conflated two different concerns. It exists to bound the **paid**
semantic (LLM) pass (I7 = cost control). Deterministic findings are **free** (pure Postgres) and are
**already** bounded by their own scan caps (`_ORPHAN_SCAN_MAX_PAGES` and `_BROKEN_SCAN_MAX_LINKS`,
each 1000). Throttling free findings with the cost cap was wrong.

**Decision:** `LINT_MAX_FINDINGS` bounds the **semantic tail only**. `run_lint_scan` records
`det_baseline = len(findings)` after the deterministic phase; the loop's stop conditions and the
final truncation count **semantic additions** (`len(findings) - det_baseline`), never the total.
Deterministic findings persist in full, bounded by their per-scan caps. **Total persisted ≤
`_ORPHAN_SCAN_MAX_PAGES + _BROKEN_SCAN_MAX_LINKS + LINT_MAX_FINDINGS`** — still a hard I7 ceiling,
just not one that hides free findings. The paginated `GET /lint/findings` (page 50) surfaces the rest.

**Ordering:** the deterministic phase appends `broken-wikilink` **before** `orphan-page` so the more
actionable, visually-dominant category leads the list (matches llm_wiki's "Broken Link"-first page).

### 2.2 `apply_lint_fix` gains a `broken-wikilink` branch (I1, I5 — same safety class as missing-xref)

`apply_lint_fix` dispatches by category (ADR-0037 §5). The new branch:

- **Suggestion present** (`suggested_target != NULL`): rewrite the dangling link in the **body** of
  the referencing page (`target_page_id`), preserving alias — `[[old]] → [[Suggested]]` and
  `[[old|label]] → [[Suggested|label]]` — via a frontmatter-preserving load/dump round-trip (I5;
  the same body-only mechanism cascade-delete uses for dead-link cleanup). Then **re-persist links
  for that one page** (`persist_links`, so the row flips `dangling=False` and the resolved edge
  appears) and bump `data_version` **exactly once** (I1). Exactly one link, one page, one bump.
- **No suggestion** (`suggested_target == NULL`): **flag-only** — `status → applied` +
  `resolution_note` acknowledging the human handled it. No file write, no bump. (Identical to the
  ADR-0037 flag-only branches for orphan/contradiction/stale-claim.)

This is the **same safety class as `missing-xref`** (ADR-0037 §5, "Automatic"): a bounded,
well-understood, human-reversible body edit that never invents or deletes curated prose — it only
repoints a link the human already wrote to an existing title. K8 holds: the scan still never
applies anything; the human clicks `Fix`.

### 2.3 REST surface additions

ADR-0037 §6 shipped `POST /lint/scan`, `GET /lint/runs[/{id}]`, `GET /lint/findings`,
`POST /lint/findings/{id}/apply`, `POST /lint/findings/{id}/dismiss`. B1 adds/extends:

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/lint/scan?semantic=bool` | `semantic` **defaults `true`** (unchanged behaviour). `false` → **skip the provider pass entirely**: deterministic-only, **free** (I6/I7). Powers the `Semantic (LLM)` checkbox. |
| `GET` | `/lint/findings?category=&severity=` | New **enum-validated** filters (values from `_VALID_CATEGORIES` / `_VALID_SEVERITIES`; invalid → 422). Composes with the existing `status` filter and the cap-200 pagination. |
| `POST` | `/lint/findings/batch` | `{ids: UUID[], action: "apply"｜"dismiss"｜"send-to-review"}`. **Cap `ids ≤ 200`** (>200 → 422; I7). Executed **sequentially server-side** — no new concurrency. Returns a **per-id result list** (`{id, ok, error?}`) so a mid-batch failure never fails the whole batch. Reuses the single-id verbs; the batch endpoint is a bounded loop over them, not a new fix path. |
| `POST` | `/lint/findings/{id}/send-to-review` | Bridge a finding into the F9 review queue via `ops/review.enqueue_review()` (FNV-1a idempotency preserved — a re-bridge is a NO-OP on the same content_key). Finding `status → applied` + `resolution_note` carrying the resulting `review_item_id`. |
| `DELETE` | `/pages/{id}` | Orphan delete from the lint UI (§2.5). **UI-only, human-double-confirmed** (K8). |

### 2.4 Category → review `item_type` mapping (send-to-review)

`enqueue_review` accepts `item_type ∈ {missing-page, suggestion, contradiction, duplicate,
confirm, …}`. The bridge maps each lint category onto a **valid** existing item type — it does not
introduce a new review type:

| Lint category | Review `item_type` |
|---------------|--------------------|
| `broken-wikilink` | `missing-page` |
| `missing-page` | `missing-page` |
| `contradiction` | `contradiction` |
| `stale-claim` | `suggestion` |
| `orphan-page` | `suggestion` |
| `missing-xref` | `suggestion` |

The bridge passes the finding's page anchors (`target_page_id` / `target_title`) so
`enqueue_review`'s content-key dedup keys on the right entity. After a successful enqueue the
finding is terminal (`applied`), with the `review_item_id` recorded in `resolution_note` for
traceability.

### 2.5 `DELETE /pages/{id}` — orphan delete (K8 — human-double-confirmed, never automated)

The lint view's orphan rows get a `Delete` action (llm_wiki parity). It is a **new** page-delete
verb, distinct from cascade-delete's plan/execute pair, but it **reuses cascade-delete's cleanup
seams** rather than reinventing them:

- **Scope guard:** `wiki/` pages only. The navigation roots `index.md` / `log.md` / `overview.md`
  are **not deletable** → **409** (they are excluded from orphan detection in ADR-0037 §2.1 for the
  same reason).
- **Effect (single transaction, ONE bump):** soft-delete the `Page` row (`deleted_at`, per the
  ADR-0005 convention — never a hard delete) · remove the Qdrant point (`delete_point`, cascade-delete
  §Step 3 convention) · remove the file · **structural `index.md` cleanup** (regenerate; the deleted
  page auto-drops) · **dead `[[wikilinks]]` → plain text** in referencing pages (reuse the
  cascade-delete body-rewrite seam — I5) · append to `log.md` · bump `data_version` **exactly once**
  (I1).
- **K8 gate:** two-stage client confirm in the UI, and — critically — **no automated path ever
  calls this endpoint**. It is not wired into `run_lint_scan`, `apply_lint_fix`, the batch endpoint,
  the review sweep, or any loop. It exists solely behind a human double-confirm click.

### 2.6 Frontend affordances (I4)

The batch bar, `Select all`, per-row `Open`/`Fix`/`Delete`, the green `Suggested target:` strip, the
`Semantic (LLM)` checkbox, and the **severity group headers** (`Errors (N) / Warnings (N) /
Info (N)`) are all presentation over the endpoints above. The group headers are **synthetic rows
inside the existing TanStack-virtualised list** (I4) — grouping must not de-virtualise the list or
move heavy work onto the main thread. `Open` reuses the existing `selectPage(target_page_id,
"tree")` seam; for a broken-wikilink row that navigates to the **referencing** page (§2.1).

## 3. Persistence — Alembic migration 0024

Migration **0024** (next free; 0023 is the highest existing) adds two nullable columns to
`lint_findings`:

| Column | Type | Notes |
|--------|------|-------|
| `suggested_target` | `Text`, nullable | tolerant-resolver best-guess title for a broken-wikilink (or `NULL`). |
| `suggested_page_id` | `UUID` FK → `pages`, nullable | live page id backing `suggested_target` (or `NULL`). Uses the `UUID(as_uuid=True).with_variant(String(36), "sqlite")` pattern (SQLite unit-test variant, matching every other UUID column). |

Both are nullable — every existing category leaves them `NULL`; only `broken-wikilink` populates
them. `category` and `severity` remain **enum-by-convention** (no DB CHECK — consistent with
ADR-0037 §3.2 and `review_items`).

**I8:** the ER diagram MUST be regenerated (`make er`) so `docs/er/schema.mmd` matches the live
models, and `docs/api/openapi.json` regenerated for the new endpoints/params. The B1 batch is not
Done until those D-artifacts are updated and consistent (this ADR does not itself run the regen —
it records the requirement; the docs gate enforces it).

## 4. Bounds — the I7 contract (extends ADR-0037 §4)

The ADR-0037 bounds table (iteration cap, token budget, `LINT_MAX_FINDINGS`, per-call timeout,
cost logging, terminal-status-in-`finally`) is **unchanged**. B1 adds:

| Bound | Mechanism |
|-------|-----------|
| broken-wikilink scan | bounded indexed read of `links.dangling=True`; **zero provider cost**; result set subject to `LINT_MAX_FINDINGS` like every other finding path |
| `?semantic=false` | **skips the provider pass entirely** — the cheapest possible scan (deterministic only, free); no provider resolved, no `chat()` call |
| `/lint/findings/batch` | `ids ≤ 200` hard cap (>200 → 422); **sequential** server-side execution (no fan-out); per-id result list — one failing id never aborts or re-runs the batch |
| `send-to-review` | single `enqueue_review` call, FNV-1a idempotent (re-bridge = NO-OP); no loop |
| `DELETE /pages/{id}` | single bounded transaction, one `data_version` bump; **not** callable from any loop |

No new env knob is required for the batch cap; `200` is a fixed structural limit (equivalently, the
UI page size). If a future ADR raises it, it must stay a bounded constant.

## 5. Consequences

- **llm_wiki lint-view parity reached.** Issue-count badge, semantic toggle, severity groups, batch
  bar, suggested-target strip, `Open`/`Fix`/`Delete` — all backed by real endpoints, no UI stubs.
- **The "741 warnings" gap is closed for free.** broken-wikilink turns an existing DB signal
  (`links.dangling`) into visible, fixable findings at zero provider cost (I7).
- **K8 preserved end-to-end.** The scan still proposes; the human still applies. The batch bar is a
  bounded loop over the same human-gated single-id verbs. Orphan delete is human-double-confirmed
  and unreachable from any automated path.
- **One idiom, extended not forked.** broken-wikilink is a deterministic finding like `orphan-page`;
  its `Fix` is the `missing-xref` safety class; send-to-review reuses `enqueue_review`; delete reuses
  the cascade-delete cleanup seams. No parallel machinery.
- **ADR-0037 stands.** Nothing here changes the semantic loop, the bounds, or the flag-only classes;
  the flag-only orphan/contradiction/stale-claim apply behaviour is unchanged (send-to-review is an
  *additional* action, not a replacement).
- **Docs debt tracked.** ER regen (I8) and OpenAPI regen are prerequisites to Done, enforced by the
  docs gate.

## 6. Do-NOT list (extends ADR-0037 §8)

ADR-0037's ten Do-NOTs still apply. B1 adds:

11. Do **not** call `DELETE /pages/{id}` from any automated path — scan, apply, batch, review sweep,
    watcher, or any loop. It is **human-double-confirmed UI only** (K8).
12. Do **not** allow `DELETE /pages/{id}` on `index.md` / `log.md` / `overview.md` — meta pages are
    409, never deleted.
13. Do **not** let a broken-wikilink `Fix` touch more than **one link in one page** with **one**
    `data_version` bump (I1/I5); the rewrite is body-only and alias-preserving.
14. Do **not** exceed the batch cap: `/lint/findings/batch` rejects `ids > 200` (422) and runs the
    ids **sequentially** — never fan-out, never partial-fail the whole batch (I7).
15. Do **not** spend a provider call for `broken-wikilink` — it is deterministic, computed from
    `links.dangling` + the tolerant resolver (I7). When `?semantic=false`, resolve **no** provider
    at all.
16. Do **not** walk the vault filesystem to find dangling links — read the `links` table (I1).
17. Do **not** invent a new review `item_type` for the bridge — map every category onto an existing
    valid type (§2.4); preserve FNV-1a idempotency (re-bridge = NO-OP).
18. Do **not** re-emit a broken-wikilink finding that is already **open** for the same
    `(vault_id, target_page_id, target_title)` — dedup on re-scan.
19. Do **not** de-virtualise the findings list to render severity group headers — headers are
    synthetic rows inside TanStack Virtual (I4).
20. Do **not** compute `suggested_target` at apply/render time — it is resolved once **at scan time**
    and stored, so `Fix` never triggers a fresh vault-wide resolve.

---

## 7. v1.3.13 lint-parity extensions (L1–L5, ADR-0058 §7)

Closes five gaps versus nashsu/llm_wiki `lint.ts` 0.6.0. No DB migration; no new Alembic revision;
no invariant loosened.

### L1 — `no-outlinks` deterministic category

A live `wiki/%` page with **zero** outgoing wikilinks (no `links` row with `source_page_id ==
page.id`). Detected in the same deterministic phase as `orphan-page` and `broken-wikilink`, with
its own I7 scan cap (`_NO_OUTLINKS_SCAN_MAX_PAGES = 1000`). Reads the `pages` and `links` tables
only (I1 — no vault walk). index.md / log.md / overview.md excluded. Severity: **info** (L5).
Added to `_VALID_CATEGORIES` and wired into `run_lint_scan` after `_detect_orphans`.

Reference: `lint.ts:267-276`.

### L2 — `suggestion` semantic category

A question or source worth adding to the wiki. Added to `_VALID_CATEGORIES`, to the `category:`
enumeration in `_build_semantic_instruction`, and to the Definitions line (`suggestion = a question
or source worth adding to the wiki`). `_parse_findings` accepts it from the model. It is
**flag-only** (added to `_FLAG_ONLY_CATEGORIES`); no deterministic fix exists. `_CATEGORY_TO_ITEM_TYPE`
maps it to `"suggestion"`.

Reference: `lint.ts:376-381` (semantic type list).

### L3 — Fuzzy suggested_target/suggested_page_id on no-outlinks and orphan-page

Port of `lint.ts::tokenizeForSuggestion` + `suggestRelatedPage`. Three new private helpers:
`_tokenize_for_suggestion`, `_fuzzy_score`, `_fuzzy_suggest_page`, plus a bounded DB loader
`_load_candidate_pages_fuzzy` (reads `(id, title, file_path)` from `pages`, capped at
`_CANDIDATE_TITLES_MAX = 500`; I7). Called once per `_detect_orphans` / `_detect_no_outlinks`
invocation, not per finding (I1 — no N+1).

- **`no-outlinks`**: `suggested_target` = title of the best related page to link TO (direction=
  "target"). `suggested_page_id` = that page's id.
- **`orphan-page`**: `suggested_target` = title of the best SOURCE page (direction="source"); the
  one that should link to the orphan. `suggested_page_id` = its id.

Min score threshold: `_RELATED_PAGE_SUGGESTION_MIN_SCORE = 0.08`. Score = token-overlap /
sqrt(|A| × |B|) + 0.08 same-folder bonus. CJK single chars weighted at 0.35.

### L4 — Auto-fixes for `no-outlinks`, `orphan-page`, and stub-less `broken-wikilink`

Three new apply paths, each doing exactly **one** `data_version` bump (I1), **body-only** edit
(I5), and 502 on failure (finding left open):

| Category | Condition | Fix |
|----------|-----------|-----|
| `no-outlinks` | `suggested_target` present | Append `- [[suggested_target]]` under `## Related` in the page body (`_apply_no_outlinks`). |
| `no-outlinks` | no suggestion | Flag-only acknowledgement (no write). |
| `orphan-page` | `suggested_page_id` present | Append `- [[<orphan title>]]` under `## Related` in the suggested SOURCE page (`_apply_orphan_page`). |
| `orphan-page` | no suggestion | Flag-only acknowledgement (no write — same behaviour as pre-L4). |
| `broken-wikilink` | no `suggested_target` | Create a type=query, tags=[stub, lint] stub page under `queries/` via `write_wiki_page`, then call `reresolve_dangling_links` so the link connects (`_create_broken_link_stub`). |
| `broken-wikilink` | `suggested_target` present | Existing rewrite path unchanged. |

`orphan-page` is removed from `_FLAG_ONLY_CATEGORIES`; it now routes through `_apply_orphan_page`
which has an internal flag-only fallback. A shared body helper `_append_wikilink_to_body` (port of
`lint-fixes.ts::appendWikilink`) and `_write_body_back` (persist links + bump) are factored out for
reuse by both `_apply_no_outlinks` and `_apply_orphan_page`.

### L5 — Severity corrections

`orphan-page` and `no-outlinks` now emit `severity="info"` (was `"warning"` for orphan-page),
matching the reference lint.ts categorization. `broken-wikilink` retains `severity="warning"`
(no change).

### _VALID_CATEGORIES (final, v1.3.13)

```python
_VALID_CATEGORIES = frozenset({
    "orphan-page",
    "broken-wikilink",
    "missing-xref",
    "contradiction",
    "stale-claim",
    "missing-page",
    "no-outlinks",   # L1
    "suggestion",    # L2
})
```

### Do-NOT additions (extends §6)

21. Do **not** emit `no-outlinks` from the semantic pass — it is deterministic-only; `_parse_findings`
    explicitly excludes it from `semantic_categories` (alongside `orphan-page`).
22. Do **not** run the fuzzy scorer without the `_CANDIDATE_TITLES_MAX` cap — the candidate pool
    is always bounded by the existing constant (I7).
23. Do **not** call `_write_body_back` when the target link already exists in the body —
    `_append_wikilink_to_body` is idempotent; the apply seam must not write if the body is unchanged.
24. Do **not** call `write_wiki_page` for a broken-wikilink stub more than once per finding —
    exactly one page, one bump (I1).
