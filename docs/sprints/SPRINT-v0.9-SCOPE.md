# Sprint v0.9 — PM Scope Lock

> Milestone: M9 — "Trust & observability"
> Author: product-manager
> Date: 2026-07-03
> Branch: sprint/v0.9 (cut from sprint/v0.8 after v0.8.0 tag)
> Prerequisite: M8 exit criteria met (EC-M8-1..EC-M8-HCP confirmed by Emanuele).
> Source roadmap: docs/reference/ROADMAP-v0.7-v1.0.md §v0.9
> UX source: docs/reference/UX-AUDIT-2026-07.md (UXA-01..UXA-28)

---

## 0. Engineer ground rule (READ BEFORE TOUCHING ANY FILE)

**No git restore, git checkout, git stash, or any command that discards working-tree
changes.** Other agents on the same branch may have uncommitted edits that are
legitimate in-progress work. If you find changes in a file you need to edit, read them
first and integrate, do NOT discard. Escalate to orchestrator if you cannot determine
ownership of an uncommitted change.

---

## 1. Sprint Goal

Make AI activity legible and trustworthy: surface what the system costs, expose its
internal health, let the wiki improve itself (purpose coherence + schema co-evolution),
give the graph the drill-down depth it needs, and close the E2E test gap before v1.0
auth work begins. In parallel, retire the most visible UX friction in a disciplined,
design-system-first way.

---

## 2. Committed Scope

Exactly the following 9 items (2 UX waves + 7 roadmap items). Anything else is out of
scope and requires explicit PM re-approval before any token is spent on it.

Items marked **IN FLIGHT** are actively being applied by the UX quick-wins team at
scope-lock time; they count toward the sprint's exit criteria but do not block Wave 1
start for the remaining items.

---

### W0 — UX quick wins (IN FLIGHT)

| Field | Value |
|---|---|
| Feature ID | F1 (UI shell), F16 (i18n / dark-mode) |
| Owner | frontend-engineer (applying audit quick wins; IN FLIGHT at scope lock) |
| Effort | S × 10 items |

**Status:** IN FLIGHT. Another team is applying these now. PM does not re-assign; PM
verifies AC completion at exit gate. No other agent touches the files listed in this
item until the in-flight work is committed and confirmed merged.

**Committed quick-win set (10 items from UX-AUDIT-2026-07.md §"Quick Wins"):**

| Sub-ID | Audit ref | File(s) touched | Change summary |
|--------|-----------|-----------------|----------------|
| W0-1 | UXA-03 | ReviewQueueView.tsx (3 sites) | Replace `color-mix(…, white)` → `color-mix(…, var(--syn-mix-base))` |
| W0-2 | UXA-05 | NavRail.tsx | Remove `outline: "none"` from inactive rail button inline style |
| W0-3 | UXA-06 | IngestRunDetail component | Add contextual hint when `pages_created === 0 && status === "completed"` |
| W0-4 | UXA-07 | graph node detail panel component | Hide UUID; replace raw `Degree N` with "Connected to N pages"; remove raw coordinate display |
| W0-5 | UXA-14 | Header.tsx, ConnectScreen.tsx | Replace `#22c55e` → `var(--syn-green)`, `#ef4444` → `var(--syn-red)` |
| W0-6 | UXA-16 | Toast.tsx | Set `role={isError ? "alert" : "status"}` on ToastItem; replace `✕` → `<X size={12} />` |
| W0-7 | UXA-17 | frontend/src/i18n/it.json | `nav.deepSearch` = `"Ricerca"` (was `"Profonda"`) |
| W0-8 | UXA-18 | ItemTypeBadge component | Normalise `item_type` underscores → hyphens before `t()` call |
| W0-9 | UXA-21 | ActivityBar.tsx | Add `t("activity.moreFailedTasks", { count })` i18n key; remove hardcoded English string |
| W0-10 | UXA-23 | ConnectScreen.tsx | Add `<CheckCircle2 size={13} color="var(--syn-green)" />` before auto-detected server hint |

**Acceptance criteria:**
- AC-W0-1: All 10 changes committed in a single PR (or sequential atomic commits) with
  the label `[F1][F16]`. Vitest passes after each change.
- AC-W0-2: `ruff check` / ESLint / prettier clean; no new TypeScript errors; no new
  `any` escapes.
- AC-W0-3: `color-mix(…, white)` regex returns zero hits in ReviewQueueView.tsx after
  W0-1 (CI grep assertion or manual verification noted in PR).
- AC-W0-4: EN and IT locale files both contain the `activity.moreFailedTasks` key with
  a `count` interpolation variable (W0-9). A Vitest snapshot asserts the translation
  renders correctly for count=1 and count=5.
- AC-W0-5: Playwright screenshot `docs/screens/ingest-zero-pages.png` captured showing
  the contextual hint (W0-3). This is also a D5 artifact.

---

### UXB-1 — Conversation auto-titles + list preview snippet

| Field | Value |
|---|---|
| Feature ID | F6 (multi-conversation chat), F16 (UX — UXA-02) |
| Owner | backend-engineer (title generation endpoint) + frontend-engineer (ConversationList preview) |
| Effort | M |

**Design decision (PM-locked):**
Title generation is server-side, triggered after the FIRST user/assistant exchange
completes (not during streaming — respects I3). The provider generates a short title
from the first user message; the call is bounded (max 1 completion, max 60 tokens,
single provider call — never retried, never streamed). On any provider error the title
falls back to a timestamp string `"Chat YYYY-MM-DD HH:mm"`. This is cheap by design:
it reuses the already-configured chat provider, costs fractions of a cent, and is
fire-and-forget (does not block the user's next message).

**Acceptance criteria:**
- AC-UXB1-1: `POST /conversations/{id}/generate-title` endpoint added to `backend/app/main.py`
  (or a `conversations.py` router if that file already exists). It reads the first
  user message from the conversation, calls `provider.chat()` with the prompt
  `"Summarise this conversation topic in 5 words or fewer: {first_message}"` and a
  `max_tokens=60` bound, stores the result (stripped, max 60 chars) in the
  `conversations` table `title` column. On any exception, stores
  `"Chat {ISO datetime}"`. A pytest asserts: (a) happy path stores title ≤ 60 chars,
  (b) provider exception path stores the timestamp fallback.
- AC-UXB1-2: The frontend calls `POST /conversations/{id}/generate-title` automatically
  after the first assistant response is fully received (stream end, not per-token — I3).
  The conversation list item updates its displayed title without a full list refetch
  (Zustand slice optimistic update). A Vitest asserts the slice sets the new title on
  the correct conversation ID.
- AC-UXB1-3: `ConversationList` shows a secondary preview line: the first 80 chars of
  the first user message (client-side, no extra API call). Preview line is styled at
  9px `var(--syn-text-dim)`, single line, ellipsis overflow. The conversation list item
  height adjusts gracefully (no layout shift on virtualized list — TanStack Virtual
  item height must be fixed or measured, I4).
- AC-UXB1-4: The title generation call is NOT retried on failure. The `max_tokens=60`
  bound is enforced at the provider call site. The cost of the title call is logged to
  `total_cost_usd` on the existing conversation run record (I7).
- AC-UXB1-5: `docs/api/openapi.json` regenerated to include the new endpoint. i18n
  key `conversation.autoTitle.fallback` added in EN (`"Chat {date}"`) and IT
  (`"Chat {date}"`).
- AC-UXB1-6: Vitest snapshot of `ConversationList` with 3 conversations: one auto-titled,
  one with timestamp fallback, one with a user-edited title (from v0.7 R7-3) — asserts
  all three render the preview snippet correctly.

---

### UXB-2 — Button / input design-system consolidation

| Field | Value |
|---|---|
| Feature ID | F1 (UI shell consistency), F16 (design-system — UXA-04 + audit debt note) |
| Owner | frontend-engineer |
| Effort | M |

**Strategy decision (PM-locked — do not re-litigate without a new ADR):**
Extend `theme.css` with a `components.css` layer. No CSS-in-JS library is introduced
(audit recommendation, confirmed). The strategy follows the UX audit's §"Design-System
Debt Note" exactly:

1. Add `components.css` (new file, imported after `theme.css`) containing scoped class
   rules for the four recurring patterns identified in the audit: `.syn-button--ghost`,
   `.syn-meta-row`, `.syn-card-row`, `.syn-role-label`.
2. Strict inline-style rule (encoded as a PR checklist item, not a lint rule in this
   sprint): no inline style may reference a raw hex color or a raw pixel value that is
   not a `--syn-*` token. Layout pixel values (widths, flex gaps) remain acceptable
   inline.
3. Refactor priority order (audit §"Audit priority order"): ReviewQueueView.tsx first
   (highest inline-style count, dark mode risk), then ActivityBar.tsx (keyframes),
   then Header.tsx (server chip), then MessageList.tsx (`Save to wiki` / `Regenerate`).
4. Add `--syn-bg-card` token explicitly to `theme.css` (it is currently undefined and
   silently falls back — audit finding §4).
5. Move `@keyframes spin`, `@keyframes syn-spin`, and `@keyframes taskBarSweep` from
   inline `<style>` tags in ActivityBar.tsx and ReviewQueueView.tsx to `theme.css`
   (audit UXA-28).

**Acceptance criteria:**
- AC-UXB2-1: `frontend/src/styles/components.css` created and imported in the app
  entry point. Contains at minimum: `.syn-button--ghost`, `.syn-meta-row`,
  `.syn-card-row`, `.syn-role-label` class definitions derived from the most common
  inline-style patterns in the refactored files.
- AC-UXB2-2: `Save to wiki` button (MessageList.tsx) and `Regenerate` button
  (MessageList.tsx) use `.syn-button.syn-button--ghost` (or `.syn-button--secondary`
  if that variant is more appropriate). ActionButton in ReviewQueueView.tsx uses the
  same class. A Vitest snapshot asserts each of these three buttons renders with the
  class attribute present.
- AC-UXB2-3: `--syn-bg-card` token added to `theme.css` (light and dark values). All
  references to `var(--syn-bg-card)` in ActivityBar.tsx and ConfirmDialog.tsx resolve
  to the explicit token, not the silent fallback.
- AC-UXB2-4: Both `@keyframes` blocks (spin/syn-spin + taskBarSweep) moved to
  `theme.css`. The inline `<style>` tags are removed from ActivityBar.tsx and
  ReviewQueueView.tsx. A Vitest that mounts both components in isolation must not
  trigger a `document.createElement("style")` call (assert via jest-spy or snapshot).
- AC-UXB2-5: After refactor, `grep -r "color-mix.*white"` returns zero hits across
  `frontend/src/` (all white-base color-mix resolved to `var(--syn-mix-base)` or
  similar token). This extends W0-1 to a tree-wide guarantee.
- AC-UXB2-6: No visual regression: Playwright screenshot comparison on the ReviewQueue,
  Chat, and Settings panels before and after the refactor (pixel diff < 1% tolerance,
  or documented intentional delta). Screenshots committed to `docs/screens/` as D5
  artifacts.
- AC-UXB2-7: TypeScript strict passes; no new `any`; ESLint + prettier clean.

**Sequencing note:** UXB-2 touches ReviewQueueView.tsx which is also touched by R9-3
and R9-4 (new ReviewItem types rendered in that view). UXB-2 MUST be merged before R9-3
and R9-4 start, so the new ReviewItem rendering uses the consolidated classes from day 1.
Assign ReviewQueueView.tsx ownership to UXB-2 engineer until UXB-2 PR is merged.

---

### R9-1 — Cost dashboard

| Field | Value |
|---|---|
| Feature ID | F17 (InferenceProvider — cost surfacing), F16 (Settings UI) |
| Owner | backend-engineer (aggregation endpoint) + frontend-engineer (Settings section) |
| Effort | M |
| Roadmap source | ROADMAP-v0.7-v1.0.md §v0.9 R9-1; CLAUDE.md §3 I7 |

**Context:** Cost is already logged per run/message in the existing `runs` table
(`total_cost_usd` column). This item aggregates that data and surfaces it in Settings.

**Acceptance criteria:**
- AC-R9-1-1: `GET /costs/summary` endpoint returns:
  ```json
  {
    "period": "YYYY-MM",
    "by_provider": [{"provider": str, "total_usd": float, "call_count": int}],
    "by_operation": [{"operation": str, "total_usd": float, "call_count": int}],
    "by_day": [{"date": "YYYY-MM-DD", "total_usd": float}],
    "monthly_total_usd": float,
    "threshold_alert": bool
  }
  ```
  Optional query param `?month=YYYY-MM` (defaults to current month).
  Aggregation runs via SQL GROUP BY on the `runs` table; no new table required unless
  the engineer identifies a performance need (must be justified in PR). A pytest with
  fixture run rows asserts correct aggregation for at least 3 providers and 2 operations.
- AC-R9-1-2: A new `COST_ALERT_THRESHOLD_USD` env var (default `5.00`) controls the
  `threshold_alert` flag. When `monthly_total_usd >= COST_ALERT_THRESHOLD_USD` the flag
  is `true`. A pytest asserts both sides of the threshold.
- AC-R9-1-3: Settings panel gains a "Cost & Usage" section showing: monthly total in USD
  (formatted as `$X.XX`), a bar chart or table of cost by provider (at minimum a table
  is acceptable for this sprint — a chart is a SHOULD, not a MUST), cost by day (sparkline
  or table row), and a red alert banner `"Monthly spend exceeds threshold ($X.XX)"` when
  `threshold_alert=true`. The section is behind the existing Settings sidebar nav.
- AC-R9-1-4: The "Cost & Usage" section polls `GET /costs/summary` once on mount; no
  auto-refresh in the background (respects I3 — no background computation while chat is
  streaming). A manual "Refresh" button is provided.
- AC-R9-1-5: `COST_ALERT_THRESHOLD_USD` is documented in `docs/DEPLOY.md` (D6b) and
  the i18n keys for the alert banner are present in EN and IT.
- AC-R9-1-6: `docs/api/openapi.json` regenerated to include `GET /costs/summary`.

**Sequencing note:** R9-1 and R9-2 both touch `backend/app/main.py`. Strict order:
R9-1 FIRST (adds the cost router or inline endpoint), then R9-2 (adds metrics endpoint).
If R9-1 extracts to `backend/app/cost.py` (a SHOULD — mirrors R8-4's export.py
pattern), R9-2's main.py conflict surface is minimized. Backend-engineer owns main.py
for both; they must be assigned sequentially to the same engineer or coordinated via
a shared feature branch.

---

### R9-2 — Metrics / health endpoint

| Field | Value |
|---|---|
| Feature ID | F16 (operational observability) |
| Owner | backend-engineer |
| Effort | S |
| Roadmap source | ROADMAP-v0.7-v1.0.md §v0.9 R9-2 |

**Acceptance criteria:**
- AC-R9-2-1: `GET /health/detailed` returns:
  ```json
  {
    "status": "ok" | "degraded" | "error",
    "watcher": {"status": str, "last_event_at": str|null, "queue_depth": int},
    "scheduler": {"status": str, "next_run_at": str|null, "last_run_at": str|null},
    "ingest_queue": {"status": str, "pending": int, "running": int, "failed_last_24h": int},
    "db": {"status": str, "latency_ms": float},
    "qdrant": {"status": str, "latency_ms": float},
    "last_errors": [{"source": str, "message": str, "at": str}]
  }
  ```
  `last_errors` contains the 5 most recent ERROR-level log entries from the in-process
  log handler (or from a `_errors` ring buffer — engineer's choice; must be documented).
  Top-level `status` is `"ok"` if all sub-systems report no error, `"degraded"` if at
  least one latency exceeds a threshold (DB > 200 ms, Qdrant > 500 ms), `"error"` if
  any sub-system is unreachable. A pytest with mocked sub-system clients asserts the
  three top-level status values.
- AC-R9-2-2: The existing `GET /health` (simple ping, returns `{"status": "ok"}`)
  is NOT changed. `GET /health/detailed` is a new endpoint at a new path.
- AC-R9-2-3: The header bar ("server chip" area in Header.tsx) gains a subtle indicator
  distinguishing `ok` (existing green dot) from `degraded` (amber dot,
  `var(--syn-warning)`) and `error` (red dot, `var(--syn-red)`). The indicator polls
  `GET /health/detailed` every 30 seconds (not on every render — use a Zustand-managed
  interval, not a useEffect timer in the component). A Vitest asserts the color mapping.
- AC-R9-2-4: `docs/api/openapi.json` regenerated. The `GET /health/detailed` schema is
  fully described (not `additionalProperties: true`).
- AC-R9-2-5: The 30-second polling interval is configurable via a `HEALTH_POLL_MS`
  frontend env var (default `30000`) documented in `docs/DEPLOY.md`. If the user is on
  mobile/narrow viewport, the polling continues but the indicator is hidden (responsive
  rule in Header.tsx).

**Sequencing note:** depends on R9-1 being merged first (main.py conflict avoidance; see
R9-1 sequencing note). Backend-engineer starts R9-2 only after R9-1 PR is merged.
Frontend engineer can build the Header indicator component in parallel (mocking the
endpoint) but must not merge until the real endpoint is live.

---

### R9-3 — purpose.md suggestions (scope drift detection)

| Field | Value |
|---|---|
| Feature ID | F2 (purpose.md context), F9 (HITL review queue) |
| Owner | ai-agent-engineer (drift detection logic + ReviewItem generation) + backend-engineer (new ReviewItem type + API) |
| Effort | M |
| Roadmap source | ROADMAP-v0.7-v1.0.md §v0.9 R9-3 |

**Design decision (PM-locked):**
After each ingest run, a bounded post-ingest check scans the newly created/modified
pages against `vault/purpose.md`. If the check finds that the new content's declared
type/topic is outside the vault's stated scope, thesis, or key questions, it generates
a `purpose-suggestion` ReviewItem with a proposed amendment to `purpose.md`. The check
is bounded: max 1 provider call per ingest run, max 300 tokens, no retry. It does NOT
auto-update `purpose.md` — the human must approve via the review queue (K8 principle).

**Acceptance criteria:**
- AC-R9-3-1: A new `ReviewItemType` enum value `purpose-suggestion` added to
  `backend/app/models.py`. Alembic migration ensures the DB check constraint accepts
  the new value. A pytest asserts the migration applies cleanly to a fresh test DB.
- AC-R9-3-2: `backend/app/ops/review.py` gains a `generate_purpose_suggestion(run_id,
  vault_id, provider)` function. It:
  (a) reads `vault/purpose.md`,
  (b) reads the titles and `type` frontmatter of pages created/modified in `run_id`,
  (c) calls `provider.chat()` with a prompt asking whether the new content is within
      scope and, if not, to propose an amendment sentence,
  (d) if the provider returns a non-empty suggestion, creates a `ReviewItem` of type
      `purpose-suggestion` with the suggestion text as the body and source_page=None,
  (e) if the content is in-scope, creates no ReviewItem (no spam in the queue).
  Bounded: `max_tokens=300`, no retry, cost logged to the ingest run record (I7).
- AC-R9-3-3: The post-ingest hook in `backend/app/ingest/orchestrator.py` calls
  `generate_purpose_suggestion` after the main ingest loop completes, before returning.
  The call is wrapped in a try/except: any exception logs a WARNING and does NOT fail
  the ingest run. A pytest integration test asserts a `purpose-suggestion` ReviewItem
  is created when fixture pages are outside the fixture purpose.md scope.
- AC-R9-3-4: The Review Queue UI (`ReviewQueueView.tsx`) renders `purpose-suggestion`
  items with a distinct badge color (`var(--syn-notice-info-bg)`) and a card header
  "Purpose drift detected". The body shows the provider's proposed amendment. Actions:
  "Apply to purpose.md" (writes the suggestion as a new paragraph in `vault/purpose.md`
  via `POST /pages` equivalent) and "Dismiss". A Vitest snapshot asserts the badge
  renders with the correct class.
- AC-R9-3-5: `docs/sequences/` updated with an addendum to the ingest sequence diagram
  showing the post-ingest purpose-check step.

**Sequencing note:** R9-3 touches `review.py` and `orchestrator.py`. R9-4 also touches
`review.py`. Strict order: R9-3 FIRST (adds `purpose-suggestion` type and
`generate_purpose_suggestion` function), then R9-4 (adds `schema-suggestion` type and
its own generate function). R9-4 engineer MUST read the post-R9-3 `review.py` before
touching it. Additionally, UXB-2 MUST be merged before R9-3 frontend work starts
(ReviewQueueView.tsx design-system consolidation first).

---

### R9-4 — schema.md co-evolution (schema-suggestion ReviewItem)

| Field | Value |
|---|---|
| Feature ID | F16 (schema governance), K6 (YAML frontmatter evolution), F9 (HITL review queue) |
| Owner | ai-agent-engineer (schema drift detection + suggestion generation) + backend-engineer (ReviewItem type, API, review.py) |
| Effort | L |
| Roadmap source | ROADMAP-v0.7-v1.0.md §v0.9 R9-4 |
| De-scope priority | **1 (first to cut if sprint runs over)** |

**Why this is the sprint's L item and de-scope priority 1:**
This extends R9-3's pattern to `schema.md` — the formal contract for frontmatter rules
(K6). It requires a more sophisticated prompt (the provider must read schema.md, observe
patterns in new pages, and propose a new frontmatter field or type value). The risk is
that the provider produces low-quality or noisy suggestions. If the sprint is under
time pressure, defer to v1.0 with no user regression (the existing schema.md remains
valid; no placeholder is needed).

**Design decision (PM-locked):**
The check fires once per ingest run, after R9-3's purpose check. It reads `vault/schema.md`
and the new/modified pages. If the pages introduce a consistent new pattern (e.g., a
frontmatter field not currently in schema.md), the provider proposes a new schema rule.
Bounded: max 1 call, max 400 tokens, no retry, no auto-apply. Human approves via review
queue (K8).

**Acceptance criteria:**
- AC-R9-4-1: A new `ReviewItemType` enum value `schema-suggestion` added to
  `backend/app/models.py` (same migration as R9-3 or a new one — engineer decides;
  document in PR). A pytest asserts the migration is idempotent.
- AC-R9-4-2: `backend/app/ops/review.py` gains a `generate_schema_suggestion(run_id,
  vault_id, provider)` function (parallel structure to `generate_purpose_suggestion`).
  It:
  (a) reads `vault/schema.md`,
  (b) reads the frontmatter of all pages created/modified in `run_id`,
  (c) calls `provider.chat()` asking whether the pages introduce a consistent new
      frontmatter pattern that should be added to schema.md, and if so to propose an
      amendment,
  (d) creates a `schema-suggestion` ReviewItem only if a non-trivial suggestion is
      returned,
  (e) if no new pattern is found, creates nothing.
  Bounded: `max_tokens=400`, no retry, cost logged (I7).
- AC-R9-4-3: The post-ingest hook in `orchestrator.py` calls `generate_schema_suggestion`
  AFTER `generate_purpose_suggestion` (R9-3 must be merged first). Same try/except
  guard — exception does not fail the ingest run.
- AC-R9-4-4: `ReviewQueueView.tsx` renders `schema-suggestion` items with badge color
  `var(--syn-notice-warning-bg)` and card header "Schema evolution proposed". Actions:
  "Apply to schema.md" and "Dismiss". The "Apply" action appends the suggestion text as
  a new section to `vault/schema.md` via the existing write path.
- AC-R9-4-5: A pytest integration test with a fixture vault asserts a `schema-suggestion`
  item is created when fixture pages contain a frontmatter field not in the fixture
  schema.md.
- AC-R9-4-6: `docs/sequences/` addendum (same doc as R9-3 if possible) shows the
  schema-check step after the purpose-check step.

**Sequencing note:** R9-4 is strictly after R9-3 on `review.py` and `orchestrator.py`.
No work starts on R9-4 until R9-3 is merged. UXB-2 must be merged before R9-4 frontend
work starts. If the sprint de-scopes R9-4, the `orchestrator.py` hook from R9-3 must
NOT contain any placeholder call to the schema function — clean exit.

---

### R9-5 — Graph drill-down (community panel + edge tooltip + cohesion score)

| Field | Value |
|---|---|
| Feature ID | F4 (knowledge graph), F1 (UX gaps #3, #11) |
| Owner | frontend-engineer (community panel + edge tooltip UI) + backend-engineer (cohesion endpoint) |
| Effort | M |
| Roadmap source | ROADMAP-v0.7-v1.0.md §v0.9 R9-5 |

**Context:** Louvain communities and FA2 layout are already implemented (v0.6). Edge
weights with 4-signal breakdown exist in the graph engine. This item surfaces that data
in the UI.

**Acceptance criteria:**
- AC-R9-5-1: `GET /graph/community/{community_id}` returns:
  ```json
  {
    "community_id": int,
    "members": [{"page_id": str, "title": str, "type": str, "degree": int}],
    "cohesion_score": float,
    "cohesion_warning": bool
  }
  ```
  `cohesion_score` = (internal edges) / (max possible internal edges); `cohesion_warning`
  = `true` when `cohesion_score < 0.2` (threshold configurable via `GRAPH_COHESION_WARN`
  env var). A pytest with a fixture graph asserts the score formula and the warning
  threshold for both sides.
- AC-R9-5-2: Clicking a community color band in the legend (or a community node/area in
  the sigma canvas) opens a side panel listing the community's member pages. The panel
  shows: community color swatch, member count, cohesion score (e.g., "Cohesion: 0.42"),
  and an amber warning banner "Low cohesion — this community may be fragmented." when
  `cohesion_warning=true`. Clicking a member navigates to that page in the editor/preview
  panel (reuses existing navigation action).
- AC-R9-5-3: Hovering an edge in the sigma canvas shows a tooltip with the 4-signal
  weight breakdown: "Direct links ×3: {value} | Source overlap ×4: {value} |
  Adamic-Adar ×1.5: {value} | Type affinity ×1: {value} | Total: {value}". The tooltip
  uses the existing tooltip component or a new `GraphEdgeTooltip` styled with
  `var(--syn-bg-card)` and `var(--syn-border)`.
- AC-R9-5-4: The edge weight tooltip data is served by `GET /graph/edge/{src_id}/{dst_id}`
  returning `{"signals": {"direct": float, "source_overlap": float, "adamic_adar": float,
  "type_affinity": float}, "total_weight": float}`. A pytest asserts the endpoint returns
  the correct values for a fixture edge.
- AC-R9-5-5: The community panel and edge tooltip obey I2: no graph recomputation on the
  UI main thread. The panel data is fetched on demand (one GET per community click); the
  edge data is fetched on hover with a 150 ms debounce to avoid thundering-herd on
  fast mouse moves.
- AC-R9-5-6: Playwright screenshot `docs/screens/graph-community-panel.png` and
  `docs/screens/graph-edge-tooltip.png` captured as D5 artifacts.

---

### R9-6 — Playwright E2E suite (happy-path + D5 screenshot refresh)

| Field | Value |
|---|---|
| Feature ID | F15 (cross-platform QA), F1 (UI coverage) |
| Owner | qa-test-engineer |
| Effort | M |
| Roadmap source | ROADMAP-v0.7-v1.0.md §v0.9 R9-6; CLAUDE.md §3 I8 |

**Context:** The `SYNAPSE_FRONTEND_URL` env var pattern is already established in
`playwright.config.ts`. D5 screenshot refresh is a DoD requirement (I8).

**Acceptance criteria:**
- AC-R9-6-1: A `frontend/tests/e2e/` directory (or expand the existing E2E test file)
  contains happy-path specs for each UI section: Connect (first-run flow), Ingest
  (upload + run + view result), Search (query + citation click), Chat (send message +
  receive response + save to wiki), Review Queue (view items + dismiss one), Graph
  (load + select node + open community panel), Settings (open Cost & Usage section +
  see monthly total). Each spec uses `SYNAPSE_FRONTEND_URL` (from `playwright.config.ts`)
  and does NOT hardcode `localhost:5173`.
- AC-R9-6-2: All 7 happy-path specs pass against a running Synapse instance (backend +
  frontend) in CI. The CI step is documented in `.github/workflows/` and runs on the
  release branch. Failures in E2E do NOT block backend unit tests (parallel jobs).
- AC-R9-6-3: Each spec that visits a visually significant state captures a screenshot
  via `page.screenshot()` and saves it to `docs/screens/{section}-{state}.png`. These
  files are committed to the repo as D5 artifacts. At minimum the following screenshots
  are refreshed: `ingest-section.png`, `chat-conversation.png`, `review-queue.png`,
  `graph-community-panel.png` (new, from R9-5), `settings-cost.png` (new, from R9-1).
- AC-R9-6-4: The E2E test for the Chat section asserts that after the first assistant
  response, the conversation title is no longer "Untitled" (regression coverage for
  UXB-1).
- AC-R9-6-5: A `playwright.config.ts` update documents the required env vars for CI:
  `SYNAPSE_FRONTEND_URL`, `SYNAPSE_BACKEND_URL`. A comment notes these are set in the
  CI environment via GitHub Actions secrets/vars, not hardcoded.
- AC-R9-6-6: The Playwright test report (`playwright-report/`) is added to
  `.gitignore` if not already present; only the captured screenshots (PNG) are committed.

---

## 3. Explicit sequencing order (same-file conflicts)

### review.py and orchestrator.py (R9-3 → R9-4)

Both R9-3 and R9-4 add new functions to `backend/app/ops/review.py` and new post-ingest
hook calls to `backend/app/ingest/orchestrator.py`. Strict merge order:

1. R9-3 merged to sprint/v0.9 (adds `purpose-suggestion` type, `generate_purpose_suggestion`,
   orchestrator hook)
2. R9-4 starts only after R9-3 is merged (reads post-R9-3 review.py and orchestrator.py
   before touching them)

The ai-agent-engineer owns the provider call logic in both items; the backend-engineer
owns the model/migration and API wiring. These sub-tasks can be parallelized WITHIN an
item but not across items on the shared files.

### main.py (R9-1 → R9-2)

Both R9-1 and R9-2 add new endpoints to `backend/app/main.py`. Strict merge order:

1. R9-1 merged (adds cost endpoint, preferably via new `backend/app/cost.py` router)
2. R9-2 merged after R9-1 (adds `GET /health/detailed`, small conflict surface if cost
   was extracted to its own router)

If R9-1 is extracted to `cost.py`, R9-2 only needs to add one `include_router` line
and the new endpoint — minimal conflict. Backend-engineer coordinates both.

### ReviewQueueView.tsx (UXB-2 → R9-3 → R9-4 frontend)

UXB-2 refactors ReviewQueueView.tsx for design-system consolidation. R9-3 and R9-4
both add new card rendering for new ReviewItem types. Strict merge order:

1. UXB-2 merged (clean ReviewQueueView.tsx with `.syn-button--ghost` and `components.css`)
2. R9-3 frontend (adds `purpose-suggestion` card using new classes)
3. R9-4 frontend (adds `schema-suggestion` card using new classes)

No engineer touches ReviewQueueView.tsx until the prior item in this chain is merged.

### graph backend files (R9-5)

R9-5 adds two new endpoints (`GET /graph/community/{id}`, `GET /graph/edge/{src}/{dst}`)
that read from the existing graph engine. These are read-only additions; no shared file
conflict with R9-1/R9-2. R9-5 can proceed in parallel with R9-1/R9-2.

### W0 (IN FLIGHT)

W0 must be fully committed before UXB-2 starts. UXB-2 cannot merge while W0 is still
in flight (overlapping files: ReviewQueueView.tsx appears in both W0 and UXB-2).

---

## 4. Wave plan (suggested 2-week schedule)

**Wave 1 (days 1–3):**
W0 completion and merge (IN FLIGHT — top priority to unblock UXB-2).
R9-1 backend starts (backend-engineer; cost aggregation endpoint).
R9-5 backend starts (backend-engineer can run this in parallel if separate engineer
available; otherwise R9-5 follows R9-2 on the same engineer's queue).
R9-6 spec scaffolding starts (qa-test-engineer; write specs, mock backend).

**Wave 2 (days 2–5, overlaps wave 1 tail):**
UXB-2 starts immediately after W0 merges (frontend-engineer; ReviewQueueView.tsx
first as highest-priority file per audit order).
R9-1 frontend (Settings Cost section) in parallel with UXB-2 frontend (no file
overlap between Settings and ReviewQueueView).
R9-5 frontend (community panel + edge tooltip) starts in parallel; no shared files
with UXB-2 target files.

**Wave 3 (days 4–8):**
R9-2 backend (after R9-1 merged; health endpoint).
UXB-1 (backend: title endpoint; frontend: ConversationList preview — no overlap with
UXB-2 or ReviewQueueView; can run in parallel with UXB-2).
R9-3 (ai-agent-engineer + backend-engineer; starts after UXB-2 ReviewQueueView.tsx
is merged).

**Wave 4 (days 7–11):**
R9-4 (after R9-3 merged; L effort; de-scope candidate if timeline is tight).
R9-6 full E2E pass (qa-test-engineer; all specs runnable against staging by day 9).

**Wave 5 (days 12–14):**
QA-test-engineer full pass. Tech-writer docs gate. Architect review.
PM exit-criteria sign-off. Human checkpoint. Tag v0.9.0.

---

## 5. Out of scope for v0.9

Everything not listed in §2 above is explicitly out of scope. The following items are
deferred and must NOT be built during this sprint:

| Deferred item | Target release | Reason |
|---|---|---|
| R10-1: Authentication layer | v1.0 | Structural; requires dedicated ADR first |
| R10-2: Multi-vault UI | v1.0 | Depends on R10-1 |
| R10-3: Code signing + notarization | v1.0 | Requires Apple/Windows certs + fee |
| R10-4: Desktop auto-update signing path | v1.0 | Requires R10-3 |
| R10-5: Mobile/PWA polish | v1.0 | Polish sprint |
| R10-6: MkDocs docs site | v1.0 | Optional docs sprint |
| Vault restore/import endpoint (POST /import) | v1.0 or later | R8-4 covered export only |
| UXA-01 NavRail group label ("Strumenti") | v1.0 | M effort; beyond quick-win boundary |
| UXA-08 role label hierarchy reduction | v1.0 | P2 friction; after design-system stable |
| UXA-09 panel skeleton state | v1.0 | P2 friction |
| UXA-10 review queue destructive confirm | v1.0 | M effort; design decision needed |
| UXA-11 ActivityBar two-tier status bar | v1.0 | P2; after observability items ship |
| UXA-12 provider selector deduplication | v1.0 | P2; provider config redesign scope |
| UXA-13 stale Settings layout removal | v1.0 | Needs verification first |
| UXA-15 ProviderSelector ARIA fix | v1.0 | Accessibility sprint |
| UXA-19 graph label dimming during hover | v1.0 | P3 polish |
| UXA-20 destructive button standardisation | v1.0 (partial in UXB-2) | P3 polish beyond UXB-2 scope |
| UXA-22 i18n Italian naturalness | v1.0 | P3 polish |
| UXA-24 review queue density/card separation | v1.0 | P3 polish |
| UXA-25 Unicode chevrons → Lucide | v1.0 | P3 polish |
| UXA-26 ⌘K shortcut discoverability | v1.0 | P3 polish |
| UXA-27 graph zoom control visibility | v1.0 | P3 polish |
| Any feature not assigned a Feature ID in CLAUDE.md §4 | never without new ID | Anti-scope-creep invariant |

**Never list (invariants I1–I9):** full-rescan, main-thread force layout, per-token DOM
mutation, WYSIWYG/ProseMirror, hardcoded provider or model ID, unbounded loops (all
provider calls in R9-1/R9-3/R9-4 are capped with `max_tokens` and no retry),
skipping D-artifacts, Tavily/alt-search, reimplementing local embeddings. These are
permanent blocks regardless of sprint.

---

## 6. Exit criteria for v0.9 release (EC-M9)

All 4 sign-offs required before tagging `v0.9.0`:
QA-test-engineer + Solution-architect + Tech-writer + Product-manager.

| ID | Criterion |
|---|---|
| EC-M9-1 | All committed items have all ACs green: W0 (10 quick wins verified), UXB-1 (auto-title + preview), UXB-2 (design-system consolidation), R9-1 (cost dashboard), R9-2 (health endpoint), R9-3 (purpose suggestions), R9-5 (graph drill-down), R9-6 (E2E suite). R9-4 is green if not de-scoped; if de-scoped, a gap note is logged here and it moves to v1.0 backlog. |
| EC-M9-2 | `ruff check` + `black --check` + mypy strict pass tree-wide. ESLint + prettier clean. TypeScript strict passes. No new `any` escapes. |
| EC-M9-3 | `grep -r "color-mix.*white" frontend/src/` returns zero hits (AC-UXB2-5 tree-wide guarantee). |
| EC-M9-4 | ER diagram zero-drift: `make er` output matches live schema. New `ReviewItemType` values (`purpose-suggestion`, `schema-suggestion` if not de-scoped) are reflected in the generated diagram. |
| EC-M9-5 | `docs/api/openapi.json` regenerated and current: includes `GET /costs/summary`, `GET /health/detailed`, `GET /graph/community/{id}`, `GET /graph/edge/{src}/{dst}`, `POST /conversations/{id}/generate-title`. |
| EC-M9-6 | All D5 screenshots refreshed: at minimum `ingest-zero-pages.png` (W0-3), `chat-conversation.png` (UXB-1), ReviewQueue section (UXB-2 visual check), `settings-cost.png` (R9-1), `graph-community-panel.png` (R9-5), `graph-edge-tooltip.png` (R9-5). Playwright screenshots committed to `docs/screens/`. |
| EC-M9-7 | `docs/sequences/` addendum updated with post-ingest purpose-check step (R9-3 AC-R9-3-5) and schema-check step if R9-4 shipped. |
| EC-M9-8 | `docs/DEPLOY.md` updated with `COST_ALERT_THRESHOLD_USD`, `GRAPH_COHESION_WARN`, and `HEALTH_POLL_MS` env vars documented. |
| EC-M9-9 | `docs/USER.md` updated with "Cost & Usage" section (R9-1) and "Graph communities" section (R9-5). Tech-writer sign-off on both. |
| EC-M9-10 | `vault/wiki/` remains a valid Obsidian vault after all v0.9 ops (I5/K7). Manual spot-check by owner. |
| EC-M9-11 | All 7 Playwright E2E happy-path specs pass in CI (R9-6). E2E screenshots committed. |
| EC-M9-12 | GitHub release `v0.9.0` created with desktop artifacts (macOS `.dmg`, Windows `.msi`, Linux `.AppImage`) carried forward from v0.8 build pipeline. |
| EC-M9-HCP | Human checkpoint: Emanuele verifies in a live session: (a) a new conversation auto-titles after first exchange; (b) `GET /costs/summary` returns correct monthly total; (c) `GET /health/detailed` shows correct sub-system status; (d) clicking a graph community shows member list + cohesion score; (e) hovering an edge shows the 4-signal tooltip; (f) an ingest run on out-of-scope content creates a `purpose-suggestion` in the Review Queue. |

---

## 7. Velocity note

v0.8 carried 7 items (3 chained L/M on extract.py). v0.9 carries 9 items (2 UX waves
+ 7 roadmap items), but the W0 wave is already IN FLIGHT at scope lock, reducing the
team's day-1 load. The critical-path chain this sprint is:

**review.py chain:** W0 → UXB-2 (ReviewQueueView.tsx) → R9-3 → R9-4

This is the primary scheduling risk. UXB-2's ReviewQueueView work must be completed
and merged before R9-3 frontend starts; R9-3 must be merged before R9-4 starts. Any
delay to UXB-2 compresses R9-3 and R9-4.

**De-scope order (if sprint runs over, cut in this order):**
1. R9-4 (schema-suggestion — L effort, de-scope priority 1; defer to v1.0 with no
   user regression. Clean exit: remove any placeholder from orchestrator.py if added.)
2. R9-5 edge tooltip only (ship community panel without edge weight breakdown; tooltip
   is a SHOULD relative to the community panel itself)
3. R9-6 screenshot refresh for settings-cost.png and graph-edge-tooltip.png (if R9-1
   and/or R9-5 edge tooltip are de-scoped, their screenshots are moot; remaining
   screenshots are still required)

UXB-1, UXB-2, R9-1, R9-2, R9-3, and the R9-6 E2E spec suite are committed and must
not be cut.
