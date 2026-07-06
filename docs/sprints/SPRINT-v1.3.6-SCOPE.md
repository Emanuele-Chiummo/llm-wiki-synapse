# Sprint v1.3.6 — PM Scope Lock

> Milestone: M136 — "Diagnostics, Real-time & Stability"
> Author: product-manager
> Date: 2026-07-06
> Branch: sprint/v1.3.6 (to be cut from main after v1.3.5 tag)
> Prerequisite: v1.3.5 shipped and tagged on main before this sprint starts.
> Sprint duration: 2–3 weeks

---

## 0. Engineer ground rules (READ BEFORE TOUCHING ANY FILE)

**Rule 1 — No destructive git operations.**
No git restore, git checkout, git stash, or any command that discards working-tree
changes. Other agents on the same branch may have uncommitted edits that are
legitimate in-progress work. If you find changes in a file you need to edit, read them
first and integrate. Do NOT discard. Escalate to orchestrator if you cannot resolve
ownership of an uncommitted change.

**Rule 2 — QA gate runs ci.yml's EXACT commands.**
The QA-test-engineer MUST run the following commands verbatim (matching ci.yml jobs)
before signing off on any item. No proxy commands, no shortcuts:

```bash
# Backend lint + type check (ci.yml jobs: lint, typecheck)
cd backend && ruff check app tests
cd backend && black --check app tests
cd backend && mypy app

# Frontend (ci.yml job: frontend)
cd frontend && npx tsc --noEmit
cd frontend && npm run lint
cd frontend && npm run test

# Docs gate — ER + OpenAPI drift check (ci.yml job: docs)
cd backend && python scripts/generate_er.py
cd backend && python scripts/generate_openapi.py
git diff --exit-code docs/er/schema.mmd
git diff --exit-code docs/api/openapi.json

# Mermaid validation loop (ci.yml job: docs — validate Mermaid diagrams step)
for f in docs/architecture/*.mmd docs/er/*.mmd docs/sequences/*.mmd; do
  [ -f "$f" ] || continue
  mmdc -p /tmp/puppeteer.json -i "$f" -o /tmp/mmdc-check.svg || exit 1
done
```

All six command groups must exit 0 before any item's ACs are marked green.

---

## 1. Sprint Goal

Make the running application stable, observable, and correct before taking on
design-heavy work. This sprint is exclusively bug fixes, real-time updates, ingest
visibility, and automation verification — all grounded in the live-app diagnostic
session that runs first (WS-H Phase 0). No new features, no UX redesigns, no
backend contract changes.

The sprint has seven committed items:

- WS-H: Diagnostics session (Phase 0 — prerequisite)
- WS-A: Real-time freshness for Home dashboard + Graph viewer [F16, F4, F18]
- WS-B: Review queue status-filter bug [F9]
- WS-C: Ingest progress visibility (batch %, ETA) [F3, F16]
- WS-D: Wiki-view bugs (frontmatter overlay + vault-meta tree node) [K1, K5, K6, I5]
- WS-G: Automations functional verification [K2, F3, F16]
- Docs gate update (I8)

**Items explicitly NOT in this sprint:**
- WS-E: Domain/Groups wizard (deferred to v1.3.7 — see §6)
- WS-F: Settings IA restructure (deferred to v1.3.7 — see §6)
- Any new backend REST contract changes beyond WS-C (which uses the existing
  /ingest/queue snapshot already specified in ADR-0046)
- Any new ADR beyond one optional WS-F pre-study ADR (if architect initiates it
  independently; it does not gate this sprint)
- Multi-vault work (v2.0 territory)

---

## 2. Release split decision record

### Why v1.3.6 = WS-H + WS-A + WS-B + WS-C + WS-D + WS-G

**Rationale:**

1. WS-H (diagnostics) must run first. It is the confirmation engine for the
   unconfirmed bugs (WS-B review filter, WS-D frontmatter overlay). Engineering
   effort on WS-B and WS-D without live reproduction is wasted speculation.
   Making Phase 0 the first delivery of v1.3.6 is therefore mandatory, not optional.

2. WS-A (real-time freshness) is a precision, bounded bug fix: add lightweight
   dataVersion polling (GET /status response already carries data_version) to the
   Home dashboard and the Graph viewer. No WebSocket, no new endpoint, no graph
   layout change. Fully respects I2 (server-side layout, cached) and I3 (no
   per-token heavy work). Complexity: S–M. Risk: low.

3. WS-B (review queue filter) is a single-bug investigation and fix. Root cause
   is unconfirmed (response cache keying vs DB status values); Phase 0 will confirm
   it. Fix is bounded to the frontend filter hook and/or the backend cache decorator.
   Complexity: S. Risk: low once root cause is confirmed.

4. WS-C (ingest progress) uses only the already-specified ADR-0046 snapshot fields
   (batch.done, batch.total, batch.eta_seconds, per-task phase/progress). The API
   contract is not being extended — only the UI is being wired to fields that already
   exist. Complexity: M. Risk: low.

5. WS-D (wiki-view) contains two sub-items: (7) frontmatter overlay edge-case and
   (8) vault-meta tree node. Item (7) is an unconfirmed edge case — Phase 0 either
   confirms or closes it. Item (8) is an architectural gap (schema.md and purpose.md
   are never indexed as Page records) requiring a thin read-from-disk "Vault/Meta"
   tree section. This is contained, does not touch the ingest pipeline, does not
   mutate vault state, and preserves I5 (files remain valid Obsidian vault files).
   Complexity: S+M. Risk: low.

6. WS-G (automations verification) is a verification exercise, not feature
   development. If regressions from v1.3.5 (log.md format change, frontmatter
   timestamps, schema.md completeness) broke any of the four scheduled ops (lint,
   backfill-domains, schema_review, reclassify), they must be found and fixed before
   v1.3.6 ships. This is the exit criterion for correctness. Complexity: S per fix
   found. Risk: medium (unknown until verified).

**Total scope: small-to-medium bug sweep + diagnostics. Appropriate for 2–3 weeks.**

### Why v1.3.7 = WS-E (domain wizard) + WS-F (settings restructure)

1. WS-E (full domain wizard) introduces: a guided creation flow with AI-match
   preview, promote-tag-to-domain, rename/merge, AND a backend /search filter
   contract change (domain/community filter params added to GET /search + FilterBar
   chips). The /search contract change requires a new ADR (architect sign-off
   mandatory per I3/I6 invariant gate). The wizard is a new multi-step UI component.
   Combining this with a bug-fix sprint would bloat scope and obscure regression
   signal from WS-A/B/C/D/G.

2. WS-F (full settings restructure) is the largest UX architecture change since the
   v1.2.4 Settings split (ADR-0055). Moving provider-auth config to a per-provider
   end-to-end flow changes the Settings information architecture root. It requires an
   ADR (the task brief explicitly says so). The ADR must be written and accepted by
   solution-architect before frontend-engineer implements anything. Adding this to
   v1.3.6 would push the sprint past 3 weeks and create an ADR-gated blocker that
   could strand the bug-fix items.

3. Sequencing WS-F after WS-G is also strategically correct: the automations
   verification (WS-G) may surface provider-config issues that inform the WS-F
   redesign. Diagnosing (WS-H + WS-G) before redesigning (WS-F) is the right order.

**PM verdict: the proposed split is APPROVED with the rationale above.**

---

## 3. Scope decision record — per workstream

### WS-H — Diagnostics (Phase 0, prerequisite) [F1, F9, F4, F18, K2, F16]

**IN SCOPE. Must complete before any WS-B, WS-D(7), WS-G engineering begins.**

A senior-tester Chrome session against the running v1.3.5 app:
- Console, network, and debug tools open throughout.
- Mandatory targets: reproduce or close WS-B (review filter returns identical
  results), reproduce or close WS-D(7) (frontmatter YAML overlay), verify
  WS-G automations are firing correctly (check scheduler logs, inspect output files,
  confirm log.md entries, confirm frontmatter timestamps after v1.3.5 change).
- Deliverable: a structured defect report filed in docs/process/BACKLOG.md as a
  new Phase-0 finding table. Every finding is either CONFIRMED (root cause identified)
  or CLOSED (unable to reproduce; conditions noted).
- The WS-B and WS-D(7) engineering items are BLOCKED on Phase-0 output. If a bug
  cannot be reproduced in Phase 0, its engineering item is CLOSED (no fix needed
  in this sprint) and a note is added to the backlog.
- The UI usability index (second deliverable from WS-H) is a scored checklist
  (Nielson 1–5 heuristics; 1-to-5 per screen; totals per section). This is a
  reference document only — it does NOT gate sprint completion. It is handed to
  the orchestrator as input for v1.3.7 UX work (WS-E/F).

**Anti-scope-creep note:** The diagnostics session MUST NOT trigger live fixes during
the session. Find first, then engineering addresses in a separate commit. Mixing
diagnostic and fix work blurs the root-cause record.

### WS-A — Real-time freshness [F16, F4, F18]

**IN SCOPE.**

Home dashboard (F18) and Graph viewer (F4) are both one-shot fetch-on-mount today.
The ActivityBar already polls at 1.5s/5s intervals. This workstream adds the same
lightweight dataVersion polling pattern to the two stale components.

**PM decisions (locked):**
- Poll mechanism: GET /status at an interval to be decided by the architect (suggested
  10s for Home dashboard, existing 5s for Graph — architect may unify). Compare
  data_version to the value at last fetch. Only re-fetch the section data if the
  version has changed.
- No WebSocket. No new endpoint. No graph recompute triggered by the frontend.
  The graph layout (FA2, igraph) remains server-side and is computed only when
  dataVersion changes (I2). The frontend polls to detect the version change and then
  calls the existing GET /graph endpoint.
- I3 compliance: the polling tick does NOT cause a re-render on every tick. Only
  a version-change causes a data refetch, which then causes a targeted section update
  via Zustand selectors + shallow equality (existing pattern).
- I2 compliance: no client-side layout computation introduced. None.
- The Home dashboard update on dataVersion change re-fetches GET /stats/overview
  and GET /stats/sections only.
- The Graph viewer update on dataVersion change re-fetches GET /graph only (existing
  endpoint; returns precomputed coords).

**What this is NOT:** a WebSocket implementation, a push notification system, a
polling interval <5s, a new REST endpoint, a change to graph layout computation,
or a change to the ActivityBar polling (leave it alone).

### WS-B — Review queue status-filter bug [F9]

**IN SCOPE. BLOCKED on WS-H Phase-0 reproduction.**

The "In attesa" (pending) and "Risolti" (resolved) tabs in the Review queue UI show
identical results. Static analysis found the frontend filter path looks correct
(status is passed to GET /review/queue) and the backend _status_filter_values
mapping looks correct. Root cause is unconfirmed.

**PM decisions (locked):**
- Engineering on this item does NOT start until Phase 0 confirms or closes it.
- If Phase 0 CLOSES it (cannot reproduce): this item is removed from the sprint and
  the backlog is updated with a "Cannot reproduce in v1.3.5" note.
- If Phase 0 CONFIRMS it: engineer identifies the actual root cause (one of:
  response cache not keyed by query param; DB status enum values mismatched to filter
  strings; frontend query param not reaching the backend; other). Fix is bounded to
  the smallest possible change that corrects the filter.
- No UI restructuring of the Review section in this fix. The fix is purely
  correctness — same layout, same tabs, same affordances.
- Test: a Playwright or vitest spec that verifies the two tabs return different item
  sets when both pending and resolved items exist in the DB.

**What this is NOT:** a Review UI redesign, a new proposal type, a new action on
review items, or any change to the review queue depth of F9's HITL flow.

### WS-C — Ingest progress visibility [F3, F16, ADR-0046]

**IN SCOPE.**

The "Lavori attivi" widget on the Home dashboard currently shows only "3 in corso"
(a snapshot of snapshot.processing count, concurrency=3 hardcoded). ADR-0046 already
specifies a richer snapshot payload: batch.done, batch.total, batch.eta_seconds, and
per-task phase/progress fields. These fields exist in the backend; the frontend does
not surface them.

**PM decisions (locked):**
- The backend contract (GET /ingest/queue snapshot schema from ADR-0046) is NOT
  extended. This is purely a frontend wiring change.
- The Home dashboard "Lavori attivi" widget is updated to show:
  - An overall progress bar: (batch.done / batch.total) * 100, with batch.total shown
    as the denominator.
  - An ETA display: "ETA ~Xs" computed from batch.eta_seconds, shown in the user's
    locale (IT/EN) and hidden if eta_seconds is null.
  - The per-task phase is shown inline per active task (e.g., "Analisi...",
    "Generazione...") using existing i18n keys where available; new i18n strings
    added for any phase labels not yet in the translation files.
- The ActivityBar polling interval feeds this update (existing 1.5s interval is
  acceptable for ingest progress; no new polling loop is added).
- I3 compliance: the progress bar is a pure CSS/DOM element, no canvas, no animation
  library. ETA is a formatted string. No heavy rerender per polling tick.
- I7 compliance: no new loop is added. The polling is the existing ActivityBar loop.

**What this is NOT:** a new backend endpoint, a change to the ADR-0046 queue schema,
a real-time WebSocket push, a change to the ingest concurrency model, or an overhaul
of the IngestView.

### WS-D — Wiki-view bugs [K1, K5, K6, I5]

**IN SCOPE. WS-D(7) is BLOCKED on WS-H Phase-0 reproduction. WS-D(8) is NOT blocked.**

Two sub-items:

**WS-D(7) — Frontmatter YAML overlay (edge case):**
Static analysis found that renderMarkdown.ts:190 already strips frontmatter before
rendering. The reported visual — YAML appearing fixed/overlapping while the body
scrolls — is therefore an unconfirmed edge case. Phase 0 must reproduce it.
- If Phase 0 CLOSES it: engineering item is dropped. Note added to backlog.
- If Phase 0 CONFIRMS it: engineer finds the specific file/condition that triggers
  the rendering artifact and fixes it in renderMarkdown.ts or the relevant component
  without introducing YAML into the rendered body. No parser change.

**WS-D(8) — Vault/Meta tree node (schema.md + purpose.md not indexed):**
schema.md and purpose.md are created at bootstrap but never indexed as Page records.
They do not appear in the wiki tree. This is an architectural gap — not a rendering
bug — and does NOT require Phase-0 confirmation. Engineering can begin immediately.
- Solution: a thin "Vault / Meta" tree section (always present, at the bottom of
  the tree) that reads schema.md and purpose.md directly from disk (no Postgres query
  needed — they are fixed paths). When either file is clicked, the existing file-read
  path (GET /pages/{id}/content or equivalent) renders it in the editor/preview panel.
- I5 compliance: both files are already valid Obsidian-compatible Markdown with YAML
  frontmatter. The tree node is read-only from the tree; the editor can still edit
  them if the user navigates to them.
- I1 compliance: no new ingest pipeline, no Postgres write, no Qdrant upsert for
  these meta files. They are read from disk on demand.
- The solution must NOT index schema.md and purpose.md as regular Page records via
  the watcher or ingest path. They are vault-meta, not wiki content. Escalate to
  solution-architect if the proposed implementation would require indexing them.

**What this is NOT:** a new page type, a change to the ingest pipeline, a change to
the Postgres pages table, a watcher extension, or any modification to the existing
wiki tree sorting/filtering logic beyond the addition of the meta section.

### WS-G — Automations functional verification [K2, F3, F16, F18]

**IN SCOPE.**

Four scheduled operations: lint, backfill-domains, schema_review, reclassify.
All are non-stub and wired (confirmed in the R13 audit). The v1.3.5 release changed
log.md format, frontmatter timestamps, and schema.md completeness. This workstream
verifies that none of these changes introduced regressions in the four ops.

**PM decisions (locked):**
- Phase 0 diagnostic session is the primary verification vehicle. The tester checks
  scheduler logs, inspects output artifacts (log.md entries, pages.updated_at
  timestamps, domain tags on pages, lint_findings rows), and confirms each op
  completes without error after v1.3.5.
- If regressions are found: engineering fixes them. Each fix is bounded to the
  minimum change that restores the op to its pre-v1.3.5 behavior. No feature
  additions to the scheduler ops in this sprint.
- WS-G verification must be complete and each op confirmed before the sprint exit
  criteria are met.
- Verification is documented in the Phase-0 defect report (WS-H deliverable) with
  one row per op: op name, trigger type (scheduled / manual run-now), output
  inspected, verdict (PASS / FAIL + root cause if FAIL).

**What this is NOT:** new scheduled operations, changes to op logic beyond regression
repair, changes to the scheduler persistence model (R13-4 settled that), or a
redesign of the automation UX.

---

## 4. Acceptance criteria

### WS-H acceptance criteria

| AC ID | Criterion |
|-------|-----------|
| AC-WS-H-1 | Phase-0 diagnostic session completed against live v1.3.5 app; console and network logs captured. |
| AC-WS-H-2 | Structured defect report present in docs/process/BACKLOG.md as Sprint 14 / v1.3.6 Phase-0 finding table; every finding is CONFIRMED or CLOSED with conditions noted. |
| AC-WS-H-3 | WS-B status (CONFIRMED or CLOSED) recorded; engineering proceed/drop decision documented. |
| AC-WS-H-4 | WS-D(7) status (CONFIRMED or CLOSED) recorded; engineering proceed/drop decision documented. |
| AC-WS-H-5 | WS-G automation verification rows present (one per op: lint, backfill-domains, schema_review, reclassify); each has verdict PASS or FAIL. |
| AC-WS-H-6 | UI usability index document produced and handed to orchestrator (does not gate sprint; reference input for v1.3.7). |

### WS-A acceptance criteria [F16, F4, F18]

| AC ID | Criterion |
|-------|-----------|
| AC-WS-A-1 | Home dashboard re-fetches GET /stats/overview and GET /stats/sections when dataVersion changes; content updates without a full page reload. |
| AC-WS-A-2 | Graph viewer re-fetches GET /graph when dataVersion changes; graph updates without triggering a client-side layout recompute (I2). |
| AC-WS-A-3 | Polling does NOT cause a re-render on every tick when dataVersion has not changed (I3). Verified by Zustand shallow-equality selector guard. |
| AC-WS-A-4 | No WebSocket, no new REST endpoint, no new polling loop introduced. Existing GET /status interval polling is the only mechanism. |
| AC-WS-A-5 | Polling interval is architect-approved and documented (default recommendation: 10s for Home dashboard, 5s for Graph viewer matching existing ActivityBar). |
| AC-WS-A-6 | vitest spec asserts: dataVersion-unchanged tick produces zero data fetches; dataVersion-changed tick produces exactly one fetch per data endpoint. |

### WS-B acceptance criteria [F9]

| AC ID | Criterion |
|-------|-----------|
| AC-WS-B-0 | GATE: WS-H Phase-0 confirms WS-B bug before any of AC-WS-B-1 through AC-WS-B-3 can be marked green. If Phase-0 CLOSES the bug, all WS-B ACs are marked N/A and the item is removed from the sprint. |
| AC-WS-B-1 | Root cause identified and documented in the Phase-0 defect report (one of: cache keying, DB enum mismatch, query param routing, or other). |
| AC-WS-B-2 | Fix applied; "In attesa" tab returns only items with status=pending; "Risolti" tab returns only items with status=resolved. |
| AC-WS-B-3 | Playwright or vitest spec verifies the two tabs return disjoint item sets when both pending and resolved review items exist. |

### WS-C acceptance criteria [F3, F16]

| AC ID | Criterion |
|-------|-----------|
| AC-WS-C-1 | "Lavori attivi" widget shows a progress bar: (batch.done / batch.total) * 100, rounded to nearest integer. |
| AC-WS-C-2 | ETA is displayed as "ETA ~Xs" (or equivalent localized form in IT/EN) when batch.eta_seconds is non-null; hidden when null. |
| AC-WS-C-3 | Per-active-task phase label is displayed for each in-progress task using existing i18n keys; new keys added to both EN and IT translation files for any missing phase labels. |
| AC-WS-C-4 | No new backend endpoint, no change to ADR-0046 queue schema. Verified by: OpenAPI drift check shows zero diff. |
| AC-WS-C-5 | Progress bar is a CSS element (no canvas, no animation library). I3 holds — no heavy rerender per polling tick. |
| AC-WS-C-6 | vitest spec: snapshot with batch={done:2, total:5, eta_seconds:30} renders "40%" bar and "ETA ~30s". |

### WS-D(8) acceptance criteria [K1, K6, I5]
(WS-D(7) ACs are conditional on Phase-0 confirmation — see AC-WS-D7 table)

| AC ID | Criterion |
|-------|-----------|
| AC-WS-D8-1 | "Vault / Meta" tree section appears at the bottom of the wiki tree; contains exactly two entries: schema.md and purpose.md. |
| AC-WS-D8-2 | Clicking schema.md or purpose.md in the tree navigates to the file content in the editor/preview panel using the existing file-read path. |
| AC-WS-D8-3 | schema.md and purpose.md are NOT indexed as Page records in Postgres and NOT vectorised in Qdrant. Verified by: DB query shows zero rows for these paths in pages table; Qdrant point count unchanged. |
| AC-WS-D8-4 | I5 holds: both files render correctly in the editor (valid Obsidian Markdown with YAML frontmatter). No YAML leaks into the rendered body. |
| AC-WS-D8-5 | I1 holds: no watcher extension, no ingest pipeline change. |
| AC-WS-D8-6 | If either file is absent from disk (edge case: fresh install before bootstrap completes), the tree section shows a placeholder "Not yet generated" label — no crash. |

**WS-D(7) conditional ACs (only if Phase-0 CONFIRMS the bug):**

| AC ID | Criterion |
|-------|-----------|
| AC-WS-D7-1 | Specific file or rendering condition that triggers the YAML overlay is documented in the Phase-0 report. |
| AC-WS-D7-2 | Fix applied in renderMarkdown.ts or the rendering component; the YAML block does not appear in the rendered body for the confirmed trigger file. |
| AC-WS-D7-3 | Existing renderMarkdown unit tests remain green; no regression in frontmatter stripping for well-formed files. |

### WS-G acceptance criteria [K2, F3, F16, F18]

| AC ID | Criterion |
|-------|-----------|
| AC-WS-G-1 | lint op: runs to completion (no uncaught exception), produces lint_findings rows, writes findings to the DB. Verified against live v1.3.5 vault. |
| AC-WS-G-2 | backfill-domains op: runs to completion, classifies at least one page into a domain tag, logs total_cost_usd. Verified against live vault with non-empty domain vocabulary. |
| AC-WS-G-3 | schema_review op: runs to completion, either produces a schema-suggestion ReviewItem or logs "no suggestions" — no silent failure. |
| AC-WS-G-4 | reclassify op: runs to completion, re-tags at least one page (or logs "nothing to reclassify") without error. |
| AC-WS-G-5 | If a regression is found for any op: the fix is bounded to restoring pre-v1.3.5 behavior. No logic changes beyond the regression repair. |
| AC-WS-G-6 | All four ops produce correct log.md entries in the v1.3.5 format (updated timestamps, valid frontmatter). |

---

## 5. Dependencies and sequencing

```
Phase 0 (WS-H)
  ├── confirms/closes WS-B → WS-B engineering (or drop)
  ├── confirms/closes WS-D(7) → WS-D(7) engineering (or drop)
  └── verifies WS-G automation health → WS-G regression fixes (if any)

WS-A, WS-C, WS-D(8)
  └── NOT blocked on Phase 0; engineering can begin immediately

WS-G regression fixes (if needed)
  └── Must complete before sprint exit criteria
```

**Dependency flags:**
- WS-B depends on: Phase-0 confirmation AND architect confirmation of fix approach
  if root cause is a backend cache keying issue (touches caching layer).
- WS-D(8) depends on: solution-architect sign-off if the implementation approach
  requires touching the Postgres pages table (escalation required per §3 note).
- WS-E (out of scope): depends on ADR for /search filter contract extension.
  That ADR has NOT been initiated. Do not start WS-E work in this sprint.
- WS-F (out of scope): depends on ADR for Settings IA restructure.
  That ADR has NOT been initiated. Do not start WS-F work in this sprint.

---

## 6. Deferred items (v1.3.7)

The following items from the user feedback are explicitly deferred to v1.3.7.
No engineering work on these items is permitted in this sprint. Any code proposed
for these items must be escalated to the orchestrator and blocked.

| Workstream | Deferred item | Reason |
|-----------|--------------|--------|
| WS-E | Domain/Groups wizard: guided creation, AI-match preview, promote-tag, rename/merge, click-domain-to-search filter, FilterBar chips | Requires ADR for /search contract change; requires new multi-step wizard component; design-heavy; not a bug fix. |
| WS-E | /search domain/community filter params | Requires architect ADR before any backend contract change. Not in scope until ADR is accepted. |
| WS-F | Settings IA full restructure (per-provider end-to-end config, plain-language descriptions, configured/missing states, base/advanced modes, per-provider wizard) | Requires ADR (explicitly noted in task brief); WS-G diagnosis may inform the design; must sequence after v1.3.6. |

---

## 7. Exit criteria — M136

All of the following must be MET before PM sign-off and human checkpoint:

| EC ID | Criterion | Owner |
|-------|-----------|-------|
| EC-M136-1 | Phase-0 defect report present in BACKLOG.md with all five WS-H ACs green. | qa-test-engineer |
| EC-M136-2 | WS-A: all six AC-WS-A ACs green; CI suite green. | qa-test-engineer |
| EC-M136-3 | WS-B: either all three AC-WS-B ACs green (if confirmed + fixed), or N/A verdict with Phase-0 closure note (if not reproduced). | qa-test-engineer |
| EC-M136-4 | WS-C: all six AC-WS-C ACs green; OpenAPI drift check shows zero diff. | qa-test-engineer |
| EC-M136-5 | WS-D(8): all six AC-WS-D8 ACs green. | qa-test-engineer |
| EC-M136-6 | WS-D(7): conditional ACs green (if confirmed), or N/A verdict with Phase-0 closure note. | qa-test-engineer |
| EC-M136-7 | WS-G: all six AC-WS-G ACs green; all four ops verified on live vault. | qa-test-engineer |
| EC-M136-8 | Docs gate: D2 (ER) regenerated if any migration landed; D4 (OpenAPI) regenerated; D7 ADR indexed if any ADR was written; BACKLOG.md updated; DOCS_STATUS.md gate entry added. Tech-writer sign-off. | tech-writer |
| EC-M136-9 | Architect review: all invariant compliance notes in this scope lock are verified by solution-architect (I1/I2/I3/I5 per workstream). | solution-architect |
| EC-M136-10 | All CI jobs green on the sprint/v1.3.6 branch (ruff, black, mypy, tsc, eslint, vitest, docs drift, mmdc). | devops-engineer |
| EC-M136-HCP | Human checkpoint: Emanuele reviews the live app on v1.3.6, confirms real-time freshness (WS-A), ingest progress (WS-C), vault-meta tree (WS-D8), and review tabs (WS-B if fixed). Explicitly approves the sprint before v1.3.7 planning begins. | Emanuele |

**All 4 sign-offs required before PM milestone sign-off:**
- QA green (EC-M136-1 through EC-M136-7 and EC-M136-10)
- Architect review (EC-M136-9)
- Tech-writer docs gate (EC-M136-8)
- PM sign-off (this document, updated at sprint end)

---

## 8. Anti-scope-creep gate

Any engineering proposal not listed in §3 MUST be escalated to the orchestrator
before any code is written. The following are confirmed out-of-scope for v1.3.6:

- WS-E domain wizard (any sub-item) — deferred to v1.3.7
- WS-F settings restructure (any sub-item) — deferred to v1.3.7
- New backend REST endpoints beyond existing paths
- WebSocket implementation
- Any change to graph layout computation (FA2, igraph, Louvain) — I2
- Any change to the ingest pipeline beyond WS-G regression repair
- Any new ADR (unless initiated by solution-architect independently as WS-F
  pre-study; does not gate this sprint)
- Multi-vault work

If an engineer identifies a P1 bug during the sprint that is not in the above
workstreams, they MUST escalate to the orchestrator. The orchestrator decides:
include as a bounded hotfix (with PM approval) or defer to a v1.3.5.x patch.

---

## 9. Velocity note

v1.3 (Sprint 13) was ON SCOPE. v1.3.5 (carrying the llm_wiki parity program
including K1/K4/F3 changes) appears to have introduced regressions in scheduled ops
(WS-G). This sprint absorbs that cost as a verification + repair cycle. The deferred
WS-E and WS-F items represent approximately 3–4 weeks of additional work (L effort
each), correctly sequenced to v1.3.7. Sprint v1.3.6 is calibrated at S/M scope
across 6 workstreams, appropriate for 2–3 weeks of evenings with agent assistance.
