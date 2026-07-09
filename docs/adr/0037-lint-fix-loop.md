# ADR-0037 — K2 Lint-fix loop (bounded, human-gated wiki health check)

- **Status:** Accepted
- **Date:** 2026-06-30
- **Sprint:** v0.6 (M6 — shippable; completes the third Karpathy core operation)
- **Features:** K2 (the third of the three Karpathy operations: **Ingest · Query · Lint**) ·
  K8 (human curates, LLM maintains — the lint queue is a curation surface) ·
  F4 (knowledge graph health — orphan detection reads the `links` in-degree) ·
  F9-adjacent (mirrors the review-queue proposal model, but for *maintenance*, not ingest follow-up)
- **Reference:** R2 (Karpathy LLM Wiki — periodic lint/health-check of the wiki) ·
  R1 (nashsu/llm_wiki — the lint operation as one of the three core operations)
- **Invariants owned:** **I7** (HEADLINE — the semantic loop is `for n in range(1, max_iter+1)` with a
  `token_budget` gate at the top of each round; findings capped at `LINT_MAX_FINDINGS`; per-call
  `asyncio.wait_for` timeout; `total_cost_usd` logged + $1 anomaly WARNING; status never left
  `running`) · **I6** (the semantic pass routes through `resolve_provider_config("ingest")` +
  `InferenceProvider.chat()`; no hardcoded backend/model, no `isinstance`/class-name branching) ·
  **I1** (the scan reads only the `pages` + `links` tables — never a full vault walk/rescan; apply edits
  touch only the referencing page(s) and bump `data_version` at most once per applied fix) ·
  **I5** (apply inserts `[[wikilinks]]` into page **bodies** only, via the existing enrichment seam) ·
  **K7** (any created page is Obsidian-valid frontmatter, via the lazy-generation seam)
- **Author:** solution-architect · ai-agent-engineer

---

## 1. Context

K2 is the **third Karpathy core operation**: *Ingest · Query · **Lint***. Ingest (F3) and Query (F5)
shipped in earlier sprints; Lint is the periodic **health check** that keeps the self-organizing wiki
coherent as it grows — finding orphaned pages, missing cross-references, contradictions, stale claims,
and concepts that are mentioned but have no page.

The defining design tension is **K8 (human curates, LLM maintains)**: a lint operation that *auto-fixes*
the wiki would silently rewrite a human-curated knowledge base on a schedule — exactly the loss of
control K8 forbids. So the lint operation must **propose, not apply**. This mirrors the F9 review-queue
proposal model (ADR-0034): the scan produces **findings** (proposals); a **human gate** turns a finding
into a fix.

The cost tension is **I7**: a "find all the problems" LLM pass is naturally open-ended (you can always
ask for *more* findings). The loop must be **structurally bounded** so it can never run away.

## 2. Decision

Add `backend/app/ops/lint.py` implementing a **bounded, human-gated** lint operation, plus two
persistence tables and five REST endpoints. The shape deliberately mirrors `ops/deep_research.py`
(bounded run + audit ledger) and `ops/review.py` (proposal rows + human actions) so there is **one**
team idiom for "bounded provider loop that writes a run row + proposal rows."

### 2.1 Two-phase scan: deterministic + semantic

`run_lint_scan(vault_id, max_iter?, token_budget?)`:

1. **Deterministic structural checks (no provider call, I1).**
   `orphan-page` = a live `wiki/` page with **graph in-degree 0** (no resolved incoming `[[wikilink]]`).
   Computed from the `pages` + `links` tables only (bounded indexed reads — never a vault walk).
   `index.md` / `log.md` / `overview.md` are excluded (navigation roots, not orphans).

2. **Bounded semantic pass (I6/I7).** A `for n in range(1, max_iter+1)` loop. Each round makes **one**
   `InferenceProvider.chat()` call asking for *new* findings of the four semantic categories
   (`missing-xref`, `contradiction`, `stale-claim`, `missing-page`), de-duplicates against everything
   seen so far, and **breaks early** when a round adds nothing new. A `token_budget` gate at the **top**
   of each round stops the loop before an unaffordable round (under-spend, never over). Findings are
   merged and **capped at `LINT_MAX_FINDINGS`**.

The semantic pass routes through `resolve_provider_config("ingest", vault_id)` + `resolve_provider` +
`chat()` — the **same backend-neutral surface** review/deep-research/enrichment use (I6). No provider
configured → **deterministic findings only** (no silent default backend).

### 2.2 The human gate (apply / dismiss)

The scan **never applies a fix**. Three human actions exist:

- `apply_lint_fix(finding_id)` — applies **only safe/bounded** fixes; bumps `data_version` **at most
  once** per applied fix (I1); never full-rescans.
- `dismiss_lint_finding(finding_id)` — `status → dismissed` (status change only).
- `list_lint_findings(...)` / `list_lint_runs(...)` — paginated reads.

## 3. Persistence (Alembic migration 0014)

### 3.1 `lint_runs` (mirrors `deep_research_runs`)

`id, vault_id, status[running|completed|error], max_iter, token_budget, iterations_used,
findings_count, total_cost_usd, started_at, completed_at, error_message, created_at`.
Bounds (`max_iter`, `token_budget`) are **FROZEN at INSERT** and never re-read mid-loop (I7). `status`
defaults to `running`; the terminal write is in a `finally` block — never left `running`.
Index `(vault_id, created_at)`.

### 3.2 `lint_findings` (mirrors `review_items`)

`id, lint_run_id (FK → lint_runs, ON DELETE CASCADE), vault_id, category, severity, target_page_id
(FK → pages), target_title, description, proposed_action, status[open|applied|dismissed],
resolution_note, created_at, reviewed_at`. Index `(vault_id, status, created_at)` + `(lint_run_id)`.

`category` and `status` are **enum-by-convention** (no DB CHECK — matches `review_items` /
`deep_research_runs`). UUID columns use the `UUID(as_uuid=True).with_variant(String(36), "sqlite")`
pattern so unit tests run on aiosqlite.

The ER diagram is regenerated (`make er`) so `docs/er/schema.mmd` matches the live models (I8).

## 4. Bounds — the I7 contract

| Bound | Mechanism |
|-------|-----------|
| Iteration cap | `for n in range(1, max_iter+1)` — structural; `LINT_MAX_ITER` default, frozen on the run row |
| Token budget | gate at the **top** of each round: `if accumulator.total_tokens >= token_budget: break`; `LINT_TOKEN_BUDGET` default, frozen |
| Finding count | merged findings truncated to `LINT_MAX_FINDINGS` (never an unbounded enqueue) |
| Per-call timeout | each semantic call wrapped in `asyncio.wait_for(LINT_TIMEOUT_SECONDS)` → on timeout, deterministic findings stand |
| Cost | `UsageAccumulator` ledger; `total_cost_usd` logged + persisted; `> $1.00` → WARNING anomaly |
| Terminal status | written in `finally` — never left `running` |

New env knobs: `LINT_MAX_ITER` (3), `LINT_TOKEN_BUDGET` (20_000), `LINT_MAX_FINDINGS` (50),
`LINT_TIMEOUT_SECONDS` (30.0). The provider row's `token_budget` overrides the env default when present.

## 5. Apply — deterministic vs flag-only

`apply_lint_fix` dispatches by category. The split between *automatic* and *flag-only* is the safety
boundary: we only ever apply a fix that has a **bounded, well-understood, reversible-by-the-human** seam.

| Category | Apply behaviour |
|----------|-----------------|
| `missing-xref` | **Automatic.** Reuses `ops/enrich_wikilinks.enrich_wikilinks` over the referencing page — adds the `[[target]]` link into the **body** (I5), re-indexes only that page, bumps `data_version` once (I1). |
| `missing-page` | **Automatic.** Delegates to the lazy-generation seam used by `review.create_page_from_review` (`_run_generation` → `write_wiki_page`) — bounded orchestrated loop, one `data_version` bump (I1), Obsidian-valid frontmatter (K7). 409 if no ingest provider (I6). |
| `orphan-page` | **Flag-only.** No safe automatic fix exists (linking an orphan is a *content* decision). `status → applied` + `resolution_note`; the human edits the wiki. |
| `contradiction` | **Flag-only.** Resolving a contradiction requires human judgement about which claim is correct. `status → applied` + note. |
| `stale-claim` | **Flag-only.** Deciding what supersedes a claim is a curation decision. `status → applied` + note. |

**Why these three are flag-only (and intentionally so):** an automatic edit for an orphan / contradiction
/ stale-claim would have to *invent* content or *delete* human-written content — both violate K8. Flagging
them surfaces the issue for the human without ever mutating curated knowledge. (If a future ADR adds a
deterministic fix — e.g. a "merge duplicate" operation — it can graduate a category from flag-only to
automatic without changing this contract.)

`apply_lint_fix` errors: 404 (finding absent), 409 (finding not `open`, or no ingest provider for a
fixable category — I6), 502 (a bounded fix failed; the finding is left `open` to retry/dismiss).

## 6. REST surface

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/lint/scan` | run a bounded scan → `{run, findings[]}` (200) |
| `GET` | `/lint/runs` · `/lint/runs/{id}` | paginated run history + detail |
| `GET` | `/lint/findings?status=open` | paginated findings (default `open`; cap 200 — I7) |
| `POST` | `/lint/findings/{id}/apply` | **HUMAN GATE** — apply a safe/bounded fix |
| `POST` | `/lint/findings/{id}/dismiss` | `status → dismissed` |

`docs/api/openapi.json` is regenerated; `docs/sequences/lint-fix.mmd` documents the flow (D3).

## 7. Consequences

- **K2 is complete.** All three Karpathy operations now exist as backend verticals.
- **K8 preserved.** The scan proposes; the human applies. No scheduled auto-rewrite of curated content.
- **One idiom reused.** `lint_runs`/`lint_findings` and the bounded loop mirror deep-research/review, so
  the team has a single mental model and the test harness is copy-pasteable.
- **Frontend deferred.** This ADR ships the backend vertical only; the lint panel is a separate task.
- **Recorded gap:** the CLI-delegated provider path is not engaged by the semantic pass (the scan rides
  `chat()`, which every backend implements). This matches ADR-0036's treatment of the enrichment pass.

## 8. Do-NOT list

1. Do **not** auto-apply any fix from `run_lint_scan` (human gate — K8).
2. Do **not** run the semantic loop as `while True` — it is `for n in range(1, max_iter+1)`.
3. Do **not** re-read `settings`/the DB row for bounds mid-loop (frozen at start — I7).
4. Do **not** emit an unbounded finding list (cap at `LINT_MAX_FINDINGS`).
5. Do **not** walk the vault filesystem — read `pages`/`links` only (I1).
6. Do **not** hardcode a backend or branch on `isinstance`/`provider_type` (I6).
7. Do **not** bump `data_version` more than once per applied fix (I1).
8. Do **not** apply an automatic fix for orphan/contradiction/stale-claim (flag-only — K8).
9. Do **not** leave a `lint_runs` row `running` (terminal write in `finally`).
10. Do **not** insert `[[wikilinks]]` into frontmatter — bodies only (I5; the enrichment seam enforces it).

---

## 9. Orphan detection fix and overview.md eligibility (v1.3.13 — incremental amendment)

### 9.1 L-bug1: Orphan `linked_ids` source filter

A **bug was found in `_detect_orphans`**: the `linked_ids` query was unfiltered — it counted ALL
links in the DB regardless of vault, source-page type, or deletion status. Because `index.md` links
to nearly every wiki page, the `linked_ids` set was effectively universal and almost no page appeared
as an orphan.

**Fix:** `linked_ids` is now built from a join `Link → Page` with filters:
- `Page.vault_id == vault_id`
- `Page.deleted_at IS NULL`
- `Page.file_path LIKE 'wiki/%'` (only live content pages as link sources)
- `Page.file_path NOT LIKE '%/index.md'` and `NOT LIKE '%/log.md'` (navigation roots excluded)

This matches the nashsu/llm_wiki `lint.ts` behaviour: in-degree is counted only from content-page
sources; `index.md`/`log.md` never contribute to the inbound-edge count. The fix is I1-compliant
(bounded indexed reads with the existing `pages`+`links` indexes; no vault walk).

### 9.2 L4: `overview.md` is eligible for orphan and no-outlinks detection

`overview.md` was excluded from the `_detect_orphans` and `_detect_no_outlinks` scan alongside
`index.md` and `log.md`. This was wrong: `overview.md` is generated by ingest but is otherwise a
normal wiki page and should participate in health detection.

**Fix:** the exclusion set in both detectors is now `{"index.md", "log.md"}` only.
`overview.md` is no longer excluded. `index.md` and `log.md` remain excluded (they are navigation
roots that intentionally have no inbound wikilinks from peers and may have no outlinks of their own).

Both fixes carry no DB schema impact and no new migration. Tests covering the fix are in
`backend/tests/test_lint.py` (`TestOrphanDetection`, `TestNoOutlinksDetection`).
