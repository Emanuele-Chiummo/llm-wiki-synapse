# ADR-0054 — Domain taxonomy (controlled vocabulary + ingest auto-tag) and dashboard stats API (`GET /stats/overview`, `GET /stats/sections`; `POST /ops/backfill-domains`)

- **Status:** Accepted
- **Date:** 2026-07-03
- **Sprint:** v1.2 (M12 — "Home & Insights"; R12-1 domain taxonomy + auto-tag, R12-2 dashboard stats API, R12-3 version-mismatch notice)
- **Features:** **F18** (Domain taxonomy + Home dashboard — NEW) · F16 (Settings / config surface — this ADR
  EXTENDS the ADR-0053 `app_config` allow-list with one new key) · F17 (the auto-tag classification MUST be
  provider-routed via `resolve_provider_config` — no hardcoded backend, I6) · K6 (tags as the frontmatter pivot) ·
  F4 (`degree` reuse for section top-pages)
- **Reference:** docs/sprints/SPRINT-v1.2-SCOPE.md (R12-1/R12-2/R12-3 — same committed items as this brief; if the
  scope file post-dates this ADR and diverges, the OWNER-LOCKED decisions in this ADR govern) ·
  **ADR-0053** (the `app_config` key/value override layer + `ALLOWED_CONFIG_KEYS` this ADR extends; §2.2, §2.3, §8
  allow-list/UI sync rule) · ADR-0052 (`SYNAPSE_AUTH_TOKEN` — the auth **middleware** that gates the new routes by
  construction; the `/status` OpenAPI-exempt set) · ADR-0007/0008 (`InferenceProvider` ABC + `provider_config`,
  `resolve_provider_config(operation, vault_id)`) · ADR-0009 (bounded loop defaults + `UsageAccumulator` /
  `total_cost_usd` accounting the auto-tag + backfill reuse, I7) · ADR-0034/0044 (Review Queue — `review_items.status`
  = `pending`) · ADR-0037 (Lint — `lint_findings.status` = `open`) · ADR-0012/0016 (graph `Edge` table +
  `degree` = distinct incident structural edges) · ADR-0014 (`data_version` debounce signal reused as the stats
  cache key) · costs.py / R9-1 (`GET /costs/summary` monthly aggregation this ADR REUSES, never duplicates) ·
  ADR-0036 (wikilink-enrichment post-write hook — the sibling non-fatal hook the auto-tag step sits beside)
- **Invariants owned:** **I1** (INCREMENTAL — auto-tag mutates only the just-ingested page's `tags` and writes it
  back through the SAME `write_wiki_page` primitive; **zero** vault re-scan; the SINGLE `data_version` bump per ingest
  is preserved — the auto-tag reuses the existing bump, never adds a second) · **I5** (OBSIDIAN — `domain/<Name>`
  tags live in the existing `tags[]` YAML frontmatter and round-trip as ordinary Obsidian nested tags) · **I6**
  (PLUGGABLE — the classification call is routed through `resolve_provider_config("ingest", …)` → `resolve_provider`;
  NO backend hardcoded; empty vocabulary ⇒ ZERO provider calls) · **I7** (BOUNDED — one bounded provider call per
  page for auto-tag; backfill capped by `max_pages` + `token_budget`; `total_cost_usd` logged; no unbounded loop) ·
  **I8** (DOCS — D2 ER unchanged [no new table]; D4 OpenAPI regenerated for the 3 new routes + the `/status`
  `version` field; D1 unchanged [no new container]) · **I3/I4** (stats endpoints are cheap COUNT/GROUP reads,
  no heavy compute, no per-token work — the FE renders precomputed numbers)
- **Author:** solution-architect
- **Implementers:** backend-engineer (extend `ALLOWED_CONFIG_KEYS` + `validate_value` for `domain_vocabulary` in
  `config_overrides.py`; the auto-tag hook in `ingest/orchestrator.py`; `ops/backfill_domains.py`; `stats.py` router
  with `GET /stats/overview` + `GET /stats/sections`; add `version` to `StatusResponse` read from pyproject; pytests;
  OpenAPI regen) · ai-agent-engineer (the classification prompt + `resolve_provider` routing + bounded-call /
  cost-accounting wiring — this touches F17, MANDATORY per §13) · frontend-engineer (Home dashboard builds against the
  two verbatim stats shapes in §5; domain-vocabulary editor writes ONLY via `PUT /config/app/domain_vocabulary`;
  R12-3 version-mismatch notice reads `/status.version`) · tech-writer (D4 OpenAPI, USER.md "Home & Domains")
- **Gate:** This ADR **GATES all R12-1/R12-2 backend code**. No `domain_vocabulary` allow-list entry, no auto-tag
  hook, no `ops/backfill_domains.py`, and no `/stats/*` route may be written until this ADR is **Accepted**. The
  frontend Home dashboard may start immediately against the two frozen response shapes in §5 (they will not change).

---

## 1. Context

Sprint v1.2 "Home & Insights" (F18) adds a **landing dashboard** ("Home") and a **domain taxonomy** so the owner can
see the vault at a glance and slice it by subject area (ServiceNow, SAM, Procurement, Regolamentazioni, TPRM, …).
The owner has **locked** four decisions this ADR must honour and MUST NOT reopen:

1. **Domains are a CONTROLLED VOCABULARY** of owner-edited domain tags — not free-form, not LLM-invented. Ingest
   **auto-tags** each new page against that vocabulary; the LLM may only choose from the list, never extend it.
2. **Home = dashboard = the landing screen.**
3. The feature ID is **F18**.
4. Empty/absent vocabulary ⇒ the feature is **dormant**: no provider calls at ingest, and Home shows global KPIs only.

Three properties make this a small, low-risk ADR rather than a new subsystem:

- **The storage already exists.** `pages.tags` (JSONB `list[str] | None`, migration 0018, K6 parity) is the
  frontmatter `tags[]` array. Domain membership is a **tag convention** on that column (§2.2), so **no new table and
  no ER change** (I8 — D2 unchanged). The `edges` table (`degree`), `review_items.status="pending"`,
  `lint_findings.status="open"`, `vault_state.data_version`, and the R9-1 monthly cost aggregation all already exist
  and are **read**, never re-derived.
- **The config surface already exists.** ADR-0053 built the generic `app_config` key/value override layer with an
  `ALLOWED_CONFIG_KEYS` allow-list, per-key validation, an in-memory cache, and `GET/PUT/DELETE /config/app`. The
  domain vocabulary is **one new allowed key** on that exact layer (§2.1) — no new persistence mechanism.
- **The provider seam already exists.** Auto-tag classification is one more provider-routed call through
  `resolve_provider_config("ingest", …)` → `resolve_provider` (ADR-0007/0008), accounted with `UsageAccumulator` /
  `total_cost_usd` (ADR-0009). No backend is hardcoded (I6).

**Two facts the implementer must not re-derive** (both established by prior ADRs):

1. **Auth is the ADR-0052 middleware, not a route dependency.** All three new routes (`/stats/overview`,
   `/stats/sections`, `/ops/backfill-domains`) are ordinary REST routes on the main router and are therefore
   **gated by construction** by `SynapseAuthMiddleware`. Do NOT add a per-route `Depends` (that double-gates —
   ADR-0052 §6). They are NOT in the `_OPENAPI_SECURITY_EXEMPT` set (`{"/status", "/health/detailed"}`), so they
   inherit the global `BearerAuth` security requirement in OpenAPI. `/status` stays exempt even after gaining
   `version` (§6).
2. **Allow-list and UI must not diverge (ADR-0053 §8).** Adding `domain_vocabulary` to `ALLOWED_CONFIG_KEYS` is the
   act of exposing it to the UI; the frontend domain-vocabulary editor MUST write only through
   `PUT /config/app/domain_vocabulary` (no parallel path). If the key were ever de-scoped it must be removed from the
   allow-list in the same change.

---

## 2. Decision — Domain vocabulary & tag convention

### 2.1 New `app_config` allowed key `domain_vocabulary` — **DECISION: JSON array of strings**

Extend the ADR-0053 allow-list in `config_overrides.py`. The **exact diff**:

```python
# config_overrides.py — ADR-0054 extends the ADR-0053 allow-list with ONE key (F18).
ALLOWED_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        "pdf_extractor",            # S1  (ADR-0051)
        "marker_service_url",       # S2  (ADR-0051)
        "marker_timeout_seconds",   # S3  (ADR-0051)
        "cost_alert_threshold_usd", # S4  (R9-1)
        "embeddings_enabled",       # S5  (ADR-0030)
        "embedding_format",         # S6  (ADR-0031)
        "overview_language",        # S7  (F3)
        "wikilink_enrich_enabled",  # S8  (ADR-0036)
        "domain_vocabulary",        # S9  (ADR-0054, F18) ← NEW: JSON array of domain names
    }
)

# Append to ORDERED_KEYS as well (stable GET /config/app order for the FE snapshot test):
ORDERED_KEYS: list[str] = [ ... , "wikilink_enrich_enabled", "domain_vocabulary"]
```

**Value format — DECISION: a JSON array of strings, e.g. `["ServiceNow","SAM","Procurement","Regolamentazioni","TPRM"]`.
Justification (vs comma-separated):**

- `app_config.value` is `TEXT NOT NULL` (ADR-0053 §2.1). Both formats fit TEXT. But domain **names may legitimately
  contain a comma-adjacent phrase, a space, or non-ASCII** ("Regolamentazioni", "Third-Party Risk Management"). A
  comma-separated string forces a fragile split/trim/escape convention and cannot represent a name containing a comma;
  a JSON array is unambiguous, escapes for free, and preserves Unicode (round-trips cleanly to Obsidian, I5).
- The value is validated and **parsed once at write** and **cached** by the ADR-0053 layer; readers call a typed
  accessor (below) that `json.loads` the cached string — O(1), no per-request parse of anything unbounded.
- It matches how `pages.sources`/`pages.tags` already store lists (JSONB arrays), keeping one mental model for "a
  list-valued value" across the codebase.

**Per-key validation** (add to `validate_value` in `config_overrides.py`, ADR-0053 §2.3 style — fail-closed, 422 on
violation, no write):

```
key == "domain_vocabulary":
  - MUST parse as JSON (json.loads) → else 422 "domain_vocabulary must be a JSON array of strings"
  - MUST be a list                  → else 422
  - every element MUST be a non-empty str after .strip()   → else 422
  - element count ≤ 100 (sanity cap; the UI is a small owned list)   → else 422
  - normalise: strip each name, drop empties, DEDUPE case-insensitively preserving first spelling
  - re-serialise canonically (json.dumps of the normalised list) before upsert
  - the EMPTY array "[]" is VALID and is the explicit "dormant" state (equivalent to no override)
```

A dedicated typed accessor lives beside the ADR-0053 accessors (coercion in one place, mypy-strict, no `Any`):

```python
def effective_domain_vocabulary() -> list[str]:
    """Parse the cached domain_vocabulary JSON array → list[str]; [] if unset/empty/malformed.
    Pure in-memory O(1) on the ADR-0053 cache; never touches the DB. Fail-closed to [] on any
    json/type error (a malformed stored value can only exist if it bypassed validate_value)."""
```

**Reset semantics** inherit ADR-0053 §3.3 unchanged: "clear the vocabulary" = `DELETE /config/app/domain_vocabulary`
(row removed ⇒ dormant) OR `PUT` with `"[]"` (explicit empty ⇒ dormant). Both yield `effective_domain_vocabulary()
== []`.

### 2.2 Tag convention — **DECISION: `domain/<Name>` nested tags in the existing `pages.tags`**

Domain membership is stored as **nested Obsidian tags** in the existing `pages.tags[]` JSONB array, prefixed
`domain/`:

```yaml
# wiki/concepts/incident-management.md frontmatter
tags:
  - domain/ServiceNow
  - domain/TPRM
  - workflow            # a pre-existing user tag — UNTOUCHED
```

- **Prefix `domain/` (confirmed, with the one refinement below).** `domain/` is an Obsidian **nested-tag** separator,
  so `domain/ServiceNow` renders as a first-class tag, is searchable as `#domain/ServiceNow`, and groups under
  `domain/` in Obsidian's tag pane — it round-trips through YAML frontmatter with **zero** special handling (I5). It
  cannot collide with a user's flat tag (a user tag `ServiceNow` is a different string than `domain/ServiceNow`).
- **The `<Name>` is the vocabulary entry verbatim** (post-normalisation from §2.1), including spaces/Unicode. Obsidian
  permits spaces and Unicode in tags when quoted; the auto-tag writer emits the tag exactly as it appears in the
  vocabulary so the `domain/<Name>` ↔ vocabulary-entry mapping is a byte-equality lookup (no slug ambiguity).
- **A page belongs to domain D iff `"domain/" + D` ∈ its `tags`.** This is the single membership predicate used by
  both the auto-tag step (§3) and the `/stats/sections` query (§5.2). Case-sensitive exact match against the current
  vocabulary; a `domain/*` tag whose name is NOT in the current vocabulary (owner removed it later) is treated as a
  **stale domain tag** — it is IGNORED by `/stats/sections` (no section rendered for it) and is NOT auto-removed
  (removal is a future, explicitly out-of-scope cleanup; §7 Do-NOT).
- **User tags are never mutated.** The auto-tag step only ADDs/removes `domain/*` entries (§3.3); any tag not starting
  with `domain/` is preserved verbatim.

---

## 3. Decision — Auto-tag step (ingest)

### 3.1 Provider routing — **DECISION: reuse the `"ingest"` operation config (no new operation)**

The classification call resolves its provider via the **existing** `resolve_provider_config("ingest", vault_id)` →
`resolve_provider(config_row)` (ADR-0007/0008). We do **NOT** add a fourth operation (`_VALID_OPERATIONS` stays
`{"ingest", "chat", "lint"}`). Rationale: auto-tag IS part of ingest (it runs inside the ingest pipeline, on the
just-ingested page, once per ingest); a per-operation override would add UI/config surface for a step the owner never
thinks of as separate. It inherits the ingest provider the owner already selected (Local/API/CLI) — I6 satisfied,
zero backend hardcoded. If a future need arises to route auto-tag independently, that is a follow-up ADR that adds
the operation; this ADR deliberately keeps the surface minimal.

### 3.2 Hook point — **DECISION: post-write, before the version bump is finalised; reuse the SINGLE existing bump (I1)**

The auto-tag step is a **non-fatal post-write hook** in `run_ingest_pipeline` (`ingest/orchestrator.py`), placed on
the orchestrated branch **immediately after the write loop and the `write_wiki_page` calls, in the same neighbourhood
as the ADR-0036 wikilink-enrichment hook and BEFORE `propose_reviews`** (orchestrator ~line 611–644). Precisely:

```
... write loop: for page in pages: write_wiki_page(...)   ← pages exist on disk + DB (I1 incremental)
... _update_overview(...)
[ADR-0054 AUTO-TAG HOOK]   ← here, wrapped in try/except (non-fatal), for each just-written page:
      if effective_domain_vocabulary() == []: skip entirely (DORMANT — zero provider calls, I6)
      else classify → merge domain/* tags → re-write the page frontmatter via write_wiki_page
... enrich_wikilinks(...)   (ADR-0036)
... propose_reviews(...)     (ADR-0034)
```

**Why post-write, not pre-write:** the page must already be a valid persisted `Page` (frontmatter + DB row) before we
touch its tags, so that (a) a classification failure leaves a **fully valid untagged page** (never a half-written
one), and (b) the tag write reuses the SAME idempotent `write_wiki_page` upsert primitive (I1 — updates only that
page's record + file; no re-scan). The auto-tag re-write of a single page's frontmatter is an **incremental update to
one already-indexed page**, exactly the I1-legal operation `write_wiki_page` performs on re-ingest.

**`data_version` (I1/I2) — DECISION: NO extra bump.** `run_ingest_pipeline` / `write_wiki_page` already performs the
**single** `bump_version()` for the ingest. The auto-tag re-write MUST NOT add a second bump (that would fire a
redundant GraphCache recompute for a tag-only change that does not alter the graph). The tags are folded into the
page **before or as part of** the existing final write so exactly ONE bump occurs per ingest. If, for the delegated
(CLI) route, the page is already written and bumped by the provider's own loop, the auto-tag re-write is a `pinned`-
style metadata update that MUST NOT bump again (the tag write calls the metadata-persist path without a version bump,
mirroring the position-pin precedent). One ingest ⇒ at most one `data_version` increment (AC-F16dv invariant preserved).

### 3.3 Classification contract & merge

- **One bounded provider call per just-written page** (I7). Input: the page title + a bounded slice of its body +
  the current vocabulary list. Output: a subset (0..N) of the vocabulary — **strictly** the classifier picks from the
  provided list; any returned name not in the vocabulary is **dropped** (the LLM cannot invent a domain — owner-lock
  #1). Prompt/response schema is the ai-agent-engineer's to finalise (structured JSON: `{"domains": ["ServiceNow", …]}`),
  routed through the resolved provider's structured-output path (I6 — same adapter, no hardcoded shape).
- **0 domains is a valid result** (the page fits no vocabulary domain) → the page keeps no `domain/*` tag and falls
  into the `untagged` bucket in `/stats/sections` (§5.2). This is NOT a failure.
- **Merge rule (idempotent):** compute the desired `domain/*` set = `{"domain/"+d for d in classified}`. Rewrite
  `tags` = (existing tags with all `domain/*` entries removed) ∪ (desired set), preserving all non-`domain/` user
  tags and stable ordering (user tags first, then sorted `domain/*`). Re-running ingest on an unchanged page yields
  an unchanged tag set (idempotent — required for backfill §4 and for content-hash stability).
- **Cost accounting (I7):** the classification call spends through the SAME `UsageAccumulator` as the ingest run, so
  its tokens/cost roll into the ingest run's `total_cost_usd` (ADR-0009). One structured log line per ingest:
  `auto_tag: page=<id> domains=[…] cost_usd=<…>` (log the domain NAMES — non-secret, useful; never a raw provider
  key).

### 3.4 Failure = page saved untagged, logged, never blocks ingest

The hook is wrapped exactly like the ADR-0036 enrichment hook: `try/except Exception` that logs a WARNING and
returns. A provider error, timeout, malformed classification, or budget exhaustion leaves the page **written and
valid, just without domain tags**. The ingest run's terminal status is unaffected. This is the I7 + owner-lock #4
safety property: auto-tag is advisory and can never be the reason an ingest fails.

---

## 4. Decision — Backfill (`POST /ops/backfill-domains`)

Backfills domain tags onto pages ingested before the vocabulary existed (or after the owner edits it).

### 4.1 Sync-with-cap vs background — **DECISION: background asyncio task, 202 + summary-by-polling (deep-research precedent)**

Backfill touches up-to-`max_pages` pages, each one a bounded provider call — potentially minutes of work. That is too
long for a synchronous request. We follow the **`POST /research/start` precedent** (ADR-0024): **freeze the bounds,
INSERT/return a run handle, schedule the work as an `asyncio.create_task`, respond 202**. (Lint's synchronous
`POST /lint/scan` is the counter-precedent for *short* scans; backfill is closer to deep-research in duration, so it
uses the background pattern.)

```
POST /ops/backfill-domains
body: {
  "max_pages":     <int>   | null,   # cap; null → settings default (e.g. 500). HARD upper bound enforced.
  "token_budget":  <int>   | null,   # cap; null → settings default. Stop when reached (under-spend, never over).
  "force":         <bool>            # default false — see idempotency below
}
→ 202 {
  "status": "started",
  "max_pages": <frozen int>,
  "token_budget": <frozen int>,
  "force": <bool>
}
→ 409 { "error": "backfill_already_running" }   # single-flight: only one backfill at a time per vault
→ 400 { "error": "dormant_vocabulary" }         # domain_vocabulary is [] → nothing to backfill, no provider calls
```

The final summary is surfaced by logging + (reuse) the ingest/run history the per-page re-writes already produce; a
dedicated polling endpoint is NOT introduced in this ADR (keep surface minimal — the FE polls `/stats/sections` to
watch section counts grow). The 202 body echoes the frozen bounds so the caller can display them. The completion log
line is the authoritative summary:
`backfill-domains: scanned=<n> tagged=<n> skipped=<n> failed=<n> cost_usd=<…> stopped_reason=<budget|maxpages|complete>`.

### 4.2 Bounds (I7)

- **`max_pages`** — the page candidate SELECT is `LIMIT max_pages` (bounded query, no unbounded scan). Ordered by
  `updated_at DESC` so the most-recently-touched pages are tagged first.
- **`token_budget`** — checked at the TOP of each per-page iteration (mirrors `run_lint_scan`, ADR-0037): if
  `accumulator.total_tokens >= token_budget`, stop before spending, `stopped_reason="budget"`.
- **`total_cost_usd`** logged for the whole run (I7). One accumulator for the backfill run.
- **No unbounded loop:** the loop is `for page in candidate_pages[:max_pages]` with the budget gate — structurally
  and cost-capped.

### 4.3 Idempotency

- **Default (`force=false`):** the candidate SELECT **skips any page that already has a `domain/*` tag** (WHERE the
  page's `tags` contains no element starting with `domain/`). Re-running a completed backfill does zero provider
  work and zero writes (idempotent).
- **`force=true`:** re-classifies ALL pages up to `max_pages` (owner edited the vocabulary and wants everything
  re-evaluated). Even here the §3.3 merge is idempotent per page, so a `force` run on an unchanged vocabulary
  produces unchanged tags.
- Each per-page write reuses `write_wiki_page` (I1 incremental) and the §3.2 no-extra-bump discipline; the backfill
  bumps `data_version` at most ONCE at the end (a single graph-neutral signal) or not at all — the implementer bumps
  once after the batch so a single debounced recompute (if any tag change affected downstream views) fires, never
  once-per-page.

---

## 5. Decision — Stats API (dashboard contract — FROZEN; the FE builds against these verbatim)

Both endpoints are **cheap read-only aggregations over existing tables** — COUNT / GROUP BY / a small ORDER BY LIMIT.
NO new heavy computation, NO graph recompute, NO provider call (I1/I2/I3/I6). Both are gated by
`SynapseAuthMiddleware` by construction (ADR-0052) and carry the global `BearerAuth` in OpenAPI.

**Caching — DECISION: `data_version`-keyed in-process memoisation (reuse the ADR-0014 debounce signal).** The
`/stats/overview` payload is memoised keyed on the current `vault_state.data_version` (+ the current month for the
cost slice): a bump invalidates it, so a bounded recompute happens at most once per data change, not per request. The
cost slice reuses the R9-1 aggregation (§5.1) which is itself month-bounded. `/stats/sections` is likewise memoised
on `data_version` + a hash of the current vocabulary (an edit to the vocabulary invalidates it). Both fall back to a
direct bounded query on a cache miss — the cache is an optimisation, not a correctness dependency.

### 5.1 `GET /stats/overview`

Global KPIs. `pages_total` / `pages_by_type` from `pages` (WHERE `deleted_at IS NULL`); `links_total` from `links`
(or `edges` — the implementer picks the structural-link count consistent with the graph; documented in the route);
`communities_count` = COUNT(DISTINCT `pages.community`) WHERE `community IS NOT NULL`; `review_pending` = COUNT
`review_items` WHERE `status = 'pending'` (ADR-0034); `lint_open` = COUNT `lint_findings` WHERE `status = 'open'`
(ADR-0037); `monthly_cost_usd` = the **current-month** `monthly_total_usd` from the **REUSED** R9-1 aggregation
(costs.py — call the shared helper, do NOT duplicate the query); `data_version` from `vault_state`; `recent_activity`
= the 10 most-recently-updated live pages (`ORDER BY updated_at DESC LIMIT 10`), `slug` derived server-side from the
title (the existing `[^a-z0-9]+ → "-"` lowercased slugify — NOT a DB column). **Response shape (verbatim):**

```json
{
  "pages_total": 128,
  "pages_by_type": {
    "entity": 40,
    "concept": 55,
    "source": 20,
    "synthesis": 8,
    "comparison": 5
  },
  "links_total": 342,
  "communities_count": 7,
  "review_pending": 3,
  "lint_open": 2,
  "monthly_cost_usd": 1.8421,
  "data_version": 57,
  "recent_activity": [
    {
      "page_id": "a1b2c3d4-0000-0000-0000-000000000001",
      "title": "Incident Management",
      "slug": "incident-management",
      "updated_at": "2026-07-03T09:12:44+00:00"
    }
  ]
}
```

- `pages_by_type` keys are the live `page_type` values present; a page with `page_type IS NULL` is counted under the
  key `"untyped"`. `recent_activity` is capped at 10; `monthly_cost_usd` is the current UTC month; `data_version`
  lets the FE decide whether to refetch.

### 5.2 `GET /stats/sections`

One entry per **current-vocabulary** domain, plus a virtual `untagged` bucket. For each domain D, the membership
predicate is `"domain/"+D ∈ pages.tags` (§2.2); `pages_by_type` is the type histogram within the section;
`last_activity` is `MAX(updated_at)` over the section's live pages (`null` if empty); `top_pages` are the section's
pages ordered by graph `degree` DESC (distinct incident structural `edges`, ADR-0016) then `updated_at` DESC, capped
at 5. The **`untagged` bucket** (DECISION on name/shape) is a section object with `"domain": "untagged"` collecting
every live page that has **no `domain/*` tag at all** — it is always present (even when empty) so the FE can render an
"unclassified" tile; it is listed **last**. **Response shape (verbatim):**

```json
{
  "sections": [
    {
      "domain": "ServiceNow",
      "pages_total": 42,
      "pages_by_type": {
        "concept": 25,
        "entity": 12,
        "source": 5
      },
      "last_activity": "2026-07-03T08:40:11+00:00",
      "top_pages": [
        {
          "id": "b2c3d4e5-0000-0000-0000-000000000010",
          "title": "Flow Designer",
          "slug": "flow-designer",
          "degree": 9
        }
      ]
    },
    {
      "domain": "untagged",
      "pages_total": 15,
      "pages_by_type": { "concept": 10, "entity": 5 },
      "last_activity": "2026-07-02T21:03:00+00:00",
      "top_pages": []
    }
  ]
}
```

- Domains are emitted in **vocabulary order** (the order the owner stored them in `domain_vocabulary`), then
  `untagged` last. A vocabulary domain with zero pages still appears (all-zero section) so the owner sees the empty
  domain. A **stale** `domain/*` tag (name not in the current vocabulary) is NOT emitted as a section and its pages
  fall into whatever current domains they still match, else `untagged`. When the vocabulary is **dormant** (`[]`),
  `sections` contains ONLY the `untagged` bucket (holding every live page) — this is the "global KPIs only, no domain
  slicing" state (owner-lock #4).

---

## 6. Decision — `/status` gains a `version` field (additive, R12-3 consumer)

`StatusResponse` gains one field, `version: str`, read from the backend package version in `pyproject.toml`
(`synapse-backend` `version = "1.1.0"` at time of writing; the implementer reads it via
`importlib.metadata.version("synapse-backend")` — NEVER a hardcoded literal). It is purely **additive** (existing
consumers ignore the new field) and does not change `/status`'s OpenAPI-exempt / no-auth posture (`/status` stays in
`_OPENAPI_SECURITY_EXEMPT`). R12-3 (frontend version-mismatch notice) reads `/status.version` and compares it to the
frontend build version to warn on a backend/frontend skew.

```json
{
  "vault_id": "default",
  "data_version": 57,
  "started_at": "2026-07-03T07:00:00+00:00",
  "uptime_seconds": 8123.4,
  "version": "1.2.0"
}
```

> Note: the FastAPI app currently declares `version="0.6.0"` in `main.py` (stale — a doc-title string, not the
> release version). The `/status.version` field MUST read the **package** version (pyproject), not that literal;
> aligning the FastAPI app `version=` to the package version is a nice-to-have cleanup but out of this ADR's scope.

---

## 7. Do-NOT list

1. **DO NOT** invent domains or free-form-tag. The classifier picks **only** from `domain_vocabulary`; any returned
   name not in the current vocabulary is dropped (owner-lock #1).
2. **DO NOT** make a provider call when the vocabulary is `[]` (dormant) — auto-tag skips entirely, backfill returns
   400 `dormant_vocabulary`, `/stats/sections` returns only `untagged` (owner-lock #4, I6/I7).
3. **DO NOT** add a second `data_version` bump for the auto-tag re-write — one ingest ⇒ at most one bump (§3.2, I1/I2).
4. **DO NOT** re-scan the vault or add a new table — domain membership is a tag convention on the existing
   `pages.tags`; no ER change (I1, I8, §1).
5. **DO NOT** mutate non-`domain/` user tags — the merge only replaces the `domain/*` subset (§3.3, I5).
6. **DO NOT** let auto-tag failure block or fail an ingest — non-fatal hook, page saved untagged, logged (§3.4, I7).
7. **DO NOT** run backfill unbounded or synchronously — background task, `max_pages` + `token_budget` caps, single-
   flight, `total_cost_usd` logged (§4, I7).
8. **DO NOT** duplicate the R9-1 monthly cost query — `/stats/overview.monthly_cost_usd` REUSES the costs.py
   aggregation (§5.1, I9-spirit / no reinvention).
9. **DO NOT** add a per-route auth `Depends` to `/stats/*` or `/ops/backfill-domains` — they are gated by
   `SynapseAuthMiddleware` by construction (ADR-0052 §6); they inherit global `BearerAuth`.
10. **DO NOT** add a 4th provider operation — auto-tag reuses `resolve_provider_config("ingest", …)`;
    `_VALID_OPERATIONS` is unchanged (§3.1).
11. **DO NOT** store the vocabulary as comma-separated text — it is a JSON array (§2.1); comma-separated cannot
    represent names with commas and mangles Unicode round-trips.
12. **DO NOT** auto-remove stale `domain/*` tags (vocabulary entry deleted) in this ADR — they are ignored by
    `/stats/sections`; cleanup is a future ADR.
13. **DO NOT** hardcode the backend version in `/status.version` — read it from pyproject via `importlib.metadata` (§6).
14. **DO NOT** let the FE write the vocabulary anywhere but `PUT /config/app/domain_vocabulary` (ADR-0053 §8 allow-
    list/UI sync; no parallel persistence path).
15. **DO NOT** skip the OpenAPI regen — the 3 new routes (+ `/status.version`) MUST be in `docs/api/openapi.json`
    drift-clean, the 3 routes carrying `BearerAuth` (I8).

---

## 8. Acceptance checks (DoD)

1. **ADR accepted before code** — this file is Accepted and in the ADR index before any R12-1/R12-2 backend code.
2. **Allow-list extended** — `domain_vocabulary` ∈ `ALLOWED_CONFIG_KEYS` + `ORDERED_KEYS`; `GET /config/app` lists it;
   `validate_value` enforces §2.1 (JSON-array-of-non-empty-strings, ≤100, dedupe); a bad value → 422 no write; `[]`
   is valid (dormant).
3. **Tag convention** — a pytest asserts `domain/<Name>` tags round-trip through frontmatter write→read and that user
   tags are preserved by the merge; membership predicate = `"domain/"+D ∈ tags` (§2.2, I5).
4. **Auto-tag hook** — orchestrated ingest with a non-empty vocabulary adds the classified `domain/*` tags via
   `write_wiki_page`; with `[]` makes ZERO provider calls; a forced provider error leaves the page written+untagged
   and the ingest `completed`; exactly one `data_version` bump per ingest (§3, I1/I6/I7).
5. **Backfill** — `POST /ops/backfill-domains` returns 202 + frozen bounds; respects `max_pages` + `token_budget`;
   `force=false` skips already-`domain/*`-tagged pages (idempotent); logs `total_cost_usd`; dormant vocabulary → 400;
   concurrent call → 409 (§4, I7).
6. **`/stats/overview`** — returns the §5.1 shape verbatim from existing tables; `monthly_cost_usd` equals the R9-1
   current-month total (reused helper); `recent_activity` capped at 10 with derived slugs; memoised on
   `data_version` (§5.1).
7. **`/stats/sections`** — returns the §5.2 shape verbatim; one section per vocabulary domain in vocabulary order +
   `untagged` last; empty domains present; `top_pages` ordered by `degree` DESC capped at 5; dormant vocabulary ⇒
   only `untagged` (§5.2).
8. **`/status.version`** — additive field read from pyproject via `importlib.metadata`; `/status` stays auth-exempt;
   no hardcoded literal (§6).
9. **Auth by construction** — the 3 new routes require `Authorization: Bearer` when `SYNAPSE_AUTH_TOKEN` is set and
   are open otherwise; NO per-route dependency; OpenAPI shows `BearerAuth` on all 3 (ADR-0052).
10. **OpenAPI (I8)** — `docs/api/openapi.json` regenerated drift-clean with the 3 routes + `/status.version`; ER (D2)
    unchanged (no new table).
11. **mypy strict / lint** — new code (`stats.py`, `ops/backfill_domains.py`, the `config_overrides.py` diff, the
    orchestrator hook) passes `ruff` + `black --check` + `mypy` (strict), no `Any`.

---

## 9. Consequences

**Positive** — the owner gets a Home dashboard and a domain-sliced view of the vault with **zero new tables** (domain
membership is a tag convention on the existing `pages.tags`, D2 ER untouched), **zero new persistence mechanism** (the
vocabulary is one key on the proven ADR-0053 `app_config` layer), and **zero new provider seam** (auto-tag reuses the
`resolve_provider_config("ingest")` route, I6). The feature is **dormant by default** — an owner who never sets a
vocabulary sees byte-identical pre-v1.2 behaviour and pays for zero extra provider calls. The stats endpoints are
cheap reads over existing counters, cached on the existing `data_version` signal, so the dashboard adds no heavy
compute (I3). The FE can start immediately against the two frozen shapes in §5.

**Trade-offs (explicit)** —
- **Auto-tag adds one provider call per ingested page** when a vocabulary is set — bounded and cost-accounted (I7),
  folded into the ingest run's `total_cost_usd`, and skippable (dormant). This is the deliberate cost of runtime
  classification; the owner controls it by controlling the vocabulary.
- **Domain membership is a soft convention, not a foreign key.** A `domain/*` tag can go stale when the owner edits
  the vocabulary; we IGNORE stale tags in stats rather than eagerly cleaning them (§2.2, Do-NOT #12) — simpler and
  correct now, with cleanup deferred to a future ADR. The trade is a small amount of dormant tag data vs a re-scan
  we refuse to do (I1).
- **Backfill is background, not synchronous** — the caller gets a 202 and watches section counts grow, rather than a
  synchronous summary. This matches the deep-research precedent and keeps the request bounded; the completion log line
  is the authoritative summary (§4.1).
- **Vocabulary as JSON-in-TEXT** — one parse at write + one at cache read; chosen over comma-separated for Unicode/
  comma safety and consistency with `pages.tags` (§2.1). The cost is a `json.loads` in the typed accessor, O(list).
- **`/stats` caching is best-effort** — memoised on `data_version`; a cache miss falls back to a bounded direct
  query, so correctness never depends on the cache.

**Invariant check** — **I1:** no vault re-scan; auto-tag mutates only the just-ingested page via `write_wiki_page`;
backfill is `LIMIT max_pages`; one `data_version` bump per ingest. **I5:** `domain/<Name>` are ordinary Obsidian
nested tags in existing frontmatter; user tags preserved. **I6:** classification routed through
`resolve_provider_config("ingest")` → `resolve_provider`; no backend hardcoded; dormant ⇒ zero calls. **I7:** one
bounded call per page for auto-tag; backfill `max_pages` + `token_budget` + single-flight; `total_cost_usd` logged;
no unbounded loop. **I8:** D4 OpenAPI regenerated (3 routes + `/status.version`, `BearerAuth`); D2 ER unchanged (no
new table); D1 unchanged (no new container). **I3/I4:** stats are cheap COUNT/GROUP reads, memoised on `data_version`;
no heavy compute, no per-token work. **I2/I9:** graph engine untouched; costs.py + provider seam + tags column reused,
not reinvented.
