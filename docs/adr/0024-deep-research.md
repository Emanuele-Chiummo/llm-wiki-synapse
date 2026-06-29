# ADR-0024 — Deep Research: bounded multi-query SearXNG loop + ingest-seam synthesis (F10)

- **Status:** Accepted
- **Date:** 2026-06-29
- **Sprint:** v0.5 (M5 Phase 2)
- **Feature:** F10 (Deep Research loop)
- **Supersedes the F10 interface stub in:** `docs/sprints/v0.5-architecture.md` §2.3 / §6.3 (this ADR is the detailed design; the stub stands as the coherence map)
- **Resolves:** AQ-v0.5-3 (synthesis through full `ingest_file`, not direct `generate()`), AQ-v0.5-4 (`max_iter` configurable default 3, frozen per-run; `concurrency=3` hardcoded)
- **Invariants owned:** **I7** (HEADLINE — every loop path bounded), **I9** (HEADLINE — SearXNG is the only web-search backend), I6 (all inference via `InferenceProvider`), I1 (synthesis incremental via the ingest seam), I5 (Obsidian-valid output)
- **Author:** solution-architect

---

## 1. Context

F10 is the most loop-intensive feature in the entire roadmap: a self-directing research agent
that issues web queries, fetches pages, assesses whether it has gathered enough, refines, and
finally synthesizes a new source document for the vault. Three things make it dangerous and three
constraints make it safe.

**The risks:**

1. **Unbounded iteration.** A naive "keep searching until the model says it's done" loop can run
   forever and burn unbounded cost — the exact bottleneck-class failure I7 exists to prevent.
2. **Unbounded fan-out.** Each iteration generates N queries × M results × fetch; without a
   concurrency ceiling this floods SearXNG and the network.
3. **Provider/back-end leakage.** It is tempting to hardcode a web-search SaaS (Tavily, SerpAPI,
   DuckDuckGo) and a model, both of which violate the project's reuse and pluggability invariants.

**The constraints (non-negotiable):**

- **I7** — every loop has a `max_iter` cap **and** a `token_budget`; `total_cost_usd` is logged.
- **I9** — SearXNG (already running on TrueNAS, `R8`) is the **only** permitted web-search backend.
  Never Tavily / DuckDuckGo / Google / SerpAPI.
- **I6** — query generation, sufficiency assessment, and synthesis are all inference, so they MUST
  go through the resolved `InferenceProvider`. No backend hardcoded.
- **I1 / I5** — the synthesized output is **source material**, fed through the existing `ingest_file`
  seam (ADR-0003), not written directly to `vault/wiki/`.

This ADR locks the module, the public entry, the exact bound-enforcement points, the SearXNG client
contract, the data model (migration **0009**), the REST surface, and the rejection triggers — before
any engineer writes F10 code.

### 1.1 Ground truth consumed

- `ingest/orchestrator.py` exposes `ingest_file(path)` — the **single** intake seam (ADR-0003). F10
  re-enters here; it does **not** call `run_ingest_pipeline` / `generate()` directly (AQ-v0.5-3).
- `InferenceProvider` (ADR-0007, `provider/base.py`) has three domain methods. The one F10 needs is
  **`chat(messages, retrieval_context) -> AsyncIterator[str]`** — a *backend-neutral, text-in /
  text-stream-out* call, implemented for all 3 backends in Phase 1 (ADR-0022 §7 / `CliAgentProvider.chat`).
  F10's three inference steps (query-gen, assess, synthesize) are text-in/text-out prompts and ride
  this existing seam. **No new ABC method is added** (the I6/ADR-0007 contract is untouched).
- `resolve_provider_config(operation, vault_id)` (`provider_config_service.py`) resolves the provider
  row by operation precedence. F10 resolves the **`"ingest"`** operation config (deep research is an
  ingest-class operation: it produces a source document for the vault). No new operation enum value.
- `UsageAccumulator` + the `_record_usage` push convention (`provider/base.py`) is the cost ledger.
- `ingest_runs` is the ingest cost ledger; F10 gets its **own** run table (`deep_research_runs`) because
  a deep-research run is a multi-call, multi-source operation distinct from a single ingest run.

---

## 2. Decision

### 2.1 Module layout

| File | Responsibility |
|------|---------------|
| `backend/app/ops/deep_research.py` | The bounded loop + the public entry `run_deep_research(...)` + the phase functions. The **only** orchestration site. |
| `backend/app/ops/searxng.py` | A **thin** SearXNG JSON client (`R8`). The **sole** place a web-search HTTP call is made (I9 static-guard target). |

No other module imports a web-search library or calls SearXNG directly.

### 2.2 Public entry signature (locked)

```python
# backend/app/ops/deep_research.py

async def run_deep_research(
    *,
    vault_id: str,
    topic: str,
    max_iter: int | None = None,        # None → DEEP_RESEARCH_MAX_ITER (default 3); frozen on the run row
    token_budget: int | None = None,    # None → DEEP_RESEARCH_TOKEN_BUDGET (default 100_000); frozen on the run row
) -> DeepResearchResult:
    """
    Run ONE bounded deep-research operation end-to-end (S-F10-1, AC-F10-1..7).

    Pipeline (single bounded loop, all six steps in order):
      1. generate 2..MAX_QUERIES_PER_ITER sub-queries           (provider, I6)
      2. SearXNG search, concurrency == CONCURRENCY (semaphore)  (I9)
      3. fetch + extract candidate pages to markdown
      4. ASSESS sufficiency BEFORE any further query round       (provider, I6)
      5. on sufficient OR iter == max_iter → SYNTHESIZE          (provider, I6)
      6. write synthesis to raw/sources/ → ingest_file(...)      (AQ-v0.5-3, I1/I5)

    Bounds (I7) are FROZEN on the deep_research_runs row at start and never re-read mid-loop.
    Terminal status is one of: converged | max_iter_reached | budget_exhausted | error.
    total_cost_usd is accumulated and logged.
    """
```

`run_deep_research` is invoked **in the background** by `POST /research/start` (which returns 202 +
`run_id` immediately) — the same fire-and-then-poll shape as the M4-EXT scheduler (ADR-0020). It is a
single asyncio task; it is NOT spawned per query.

### 2.3 Result type + internal phase functions (locked shapes)

```python
@dataclass
class DeepResearchResult:
    run_id: uuid.UUID
    status: Literal["converged", "max_iter_reached", "budget_exhausted", "error"]
    iterations_used: int
    sources_fetched: int
    total_cost_usd: float
    synthesis_page_id: uuid.UUID | None    # the pages row created by the re-entrant ingest_file
    error_message: str | None

# ── internal phases (module-private; names locked for the D3 sequence diagram) ──
async def _generate_queries(provider, topic, prior_context, *, max_queries: int) -> list[str]: ...
async def _search_searxng(queries: list[str]) -> list[SearchHit]: ...      # uses ops/searxng.py, concurrency-bounded
async def _fetch_and_extract(hits: list[SearchHit]) -> list[FetchedSource]: ...  # concurrency-bounded
async def _assess_sufficiency(provider, topic, collected) -> Sufficiency: ...    # returns {sufficient: bool, gaps: list[str]}
async def _synthesize(provider, topic, collected) -> str: ...             # returns markdown body for raw/sources/
```

All four provider-touching phases (`_generate_queries`, `_assess_sufficiency`, `_synthesize`) call the
resolved provider's **`chat()`** with a phase-specific instruction in `retrieval_context` and parse the
returned text (queries = newline list; sufficiency = a small JSON/`SUFFICIENT|INSUFFICIENT` token;
synthesis = the markdown body). They push `Usage` via the bound `UsageAccumulator` exactly like the
ingest loop (I6/I7). **No `isinstance` / provider-type branch anywhere** (the I6 hard rule).

---

## 3. Bounds (I7) — the headline. Exact enforcement points.

There are **three** bounds plus a hardcoded fan-out ceiling. Each has a single, unmissable
enforcement site. Engineers MUST implement them exactly as shaped below; weakening any of them is a
**P0 rejection** (§5).

### 3.1 The bound constants

| Bound | Source | Default | Frozen where | Mutable mid-loop? |
|-------|--------|---------|--------------|-------------------|
| `max_iter` | `POST /research/start` body → else env `DEEP_RESEARCH_MAX_ITER` | **3** | `deep_research_runs.max_iter` at INSERT | **No** — read once into a local |
| `token_budget` | `POST /research/start` body → else env `DEEP_RESEARCH_TOKEN_BUDGET` | **100_000** | `deep_research_runs.token_budget` at INSERT | **No** |
| `max_queries_per_iter` | env `DEEP_RESEARCH_MAX_QUERIES` | **5** | local constant | **No** |
| `concurrency` | **HARDCODED module constant** `CONCURRENCY = 3` | **3** | not configurable | **No — architect-approval gate to change** |

`max_iter`, `token_budget`, `max_queries_per_iter` are read **once** at run start into local variables
(and persisted on the run row for audit). The loop NEVER re-reads config or env mid-flight. This is the
"frozen at run start" guarantee (AQ-v0.5-4).

### 3.2 The loop guard (mandated code shape)

The loop is a counted `for` with **two** internal break conditions. It is NOT a `while True`.

```python
# bounds frozen ONCE at start (AQ-v0.5-4)
max_iter      = run.max_iter            # from the row, already defaulted at INSERT
token_budget  = run.token_budget
accumulator   = UsageAccumulator()
provider.bind_accumulator(accumulator)

collected: list[FetchedSource] = []
status = "max_iter_reached"             # PESSIMISTIC DEFAULT — overwritten only on a real exit
queries = await _generate_queries(provider, topic, prior_context="", max_queries=MAX_QUERIES)

for iteration in range(1, max_iter + 1):          # ← HARD CAP (I7). range, not while True.
    run.iterations_used = iteration                # persisted each round (audit + AC-F10-2b)

    # ── budget gate BEFORE spending the round (I7) ──────────────────────────────
    if accumulator.total_tokens >= token_budget:
        status = "budget_exhausted"
        break

    hits      = await _search_searxng(queries)              # concurrency==3 (§4)
    collected += await _fetch_and_extract(hits)             # concurrency==3 (§4)

    # ── ASSESS sufficiency BEFORE deciding to refine (CLAUDE.md §7) ─────────────
    verdict = await _assess_sufficiency(provider, topic, collected)
    if verdict.sufficient:
        status = "converged"
        break

    # not sufficient AND not last iteration → refine; the for-range caps refinement
    if iteration < max_iter:
        queries = await _generate_queries(
            provider, topic, prior_context=verdict.gaps, max_queries=MAX_QUERIES
        )
    # if iteration == max_iter the loop exits with status == "max_iter_reached"

# ── NO further provider call once the loop exits on max_iter_reached EXCEPT the
#    single terminal synthesize, which runs for converged/max_iter/budget alike. ──
if status in ("converged", "max_iter_reached", "budget_exhausted"):
    synthesis_md = await _synthesize(provider, topic, collected)   # ONE call
    page_id = await _ingest_synthesis(run_id, vault_id, synthesis_md)  # §6 — re-enters ingest_file
```

**Why this shape is non-negotiable:**

- `for iteration in range(1, max_iter + 1)` — the iteration count is **structurally** capped. There is
  no code path that adds an iteration. A reviewer can confirm boundedness by reading one line.
- `status` defaults to `"max_iter_reached"` and is overwritten **only** by a real convergence/budget
  exit. If the loop falls through, the status is correct by construction (AC-F10-2b). The run is
  **never** left `"running"`.
- The sufficiency assessment is **inside** the loop, **before** the refine decision (CLAUDE.md §7
  "assess sufficiency before each query"). On the final iteration there is no refine call — assessment
  fail simply ends the loop (AC-F10-2c: "no further InferenceProvider calls after max_iter is reached"
  — the only post-loop call is the single terminal synthesize, which is the deliberate step-5 exit).
- The budget gate is checked **at the top of each round, before spending** — under-spend, never
  over-spend (the I7 conservative direction).

### 3.3 Cost accounting + anomaly

- Every provider call pushes `Usage` to the run-scoped `UsageAccumulator` (`_record_usage`), exactly
  as the ingest loop does. `total_cost_usd = round(accumulator.total_cost_usd, 4)`.
- On terminal, `total_cost_usd` and `iterations_used` are written to the `deep_research_runs` row and
  emitted in one structured log line (mirrors `ingest_run` logging).
- The **$1.00 cost-anomaly WARNING** (ADR-0009 §3) is reused: if `total_cost_usd > 1.00`, log a WARNING
  after the run row is finalized. Local/CLI cost is `0.0000` by convention (ADR-0009).

---

## 4. I9 — SearXNG client contract (`ops/searxng.py`)

The **only** web-search code in the codebase. No other module may import it transitively to reach a
non-SearXNG backend. The static guard (AC-F10-3) scans all `.py` for `tavily|duckduckgo|ddg|googlesearch|serpapi`.

```python
# backend/app/ops/searxng.py

CONCURRENCY = 3   # HARDCODED I7 ceiling — shared by search and fetch. Architect gate to change.

class SearchHit(BaseModel):
    url: str
    title: str
    snippet: str | None = None
    engine: str | None = None

async def searxng_search(query: str, *, max_results: int = 10) -> list[SearchHit]:
    """
    ONE SearXNG query → JSON results. Base URL from env SEARXNG_URL ONLY (I9).
    Calls GET {SEARXNG_URL}/search?q=<query>&format=json (the SearXNG JSON API, R8).
    No API key. No fallback to any other search provider. On non-200 → [] (logged), never an
    alternative backend.
    """

async def searxng_search_many(queries: list[str]) -> list[SearchHit]:
    """
    Run all queries with concurrency bounded by a module asyncio.Semaphore(CONCURRENCY).
    Implemented as asyncio.gather over searxng_search, each acquiring the semaphore.
    De-dupes hits by URL. This is the ONLY concurrency in F10 search.
    """
```

**Config:** a single new setting `searxng_url: str` in `config.py` (env `SEARXNG_URL`, e.g.
`http://searxng:8080`). Required when deep research is used; if unset, `POST /research/start` returns a
clean 400/503 config error (never a fake run, never a fallback search engine).

**Fetch + extract (step 3):** `_fetch_and_extract` fetches each hit URL with `httpx` (already a dep)
under the **same** `Semaphore(CONCURRENCY=3)`, and extracts readable text to markdown. For Phase 2 the
extractor is a lightweight HTML→markdown (reuse the F11/clipper Readability+Turndown approach on the
*backend* side via a minimal `html2text`-style reduction, or `unstructured` once F12 lands it). The
extractor makes **no LLM call** (I6 — extraction is mechanical). Per-source content is capped
(`DEEP_RESEARCH_FETCH_MAX_CHARS`, default 20_000) so a single huge page cannot blow the token budget.

> **I9 rule restated:** SearXNG is THE web-search backend. There is no second search path, no
> "if SearXNG down, try X". A SearXNG failure degrades to fewer/zero hits, logged — never to another
> engine.

---

## 5. Do-NOT list (rejection triggers)

A PR that does any of the following is **rejected** at architect review. Items 1–4 are **P0 blocks**
(EC-M5-5 / EC-M5-19) — they break the headline invariants.

1. **DO NOT use `while True` or any unbounded loop** for the refinement loop. The loop MUST be
   `for iteration in range(1, max_iter + 1)` with the assessment inside it. (I7 — P0)
2. **DO NOT re-read `max_iter` / `token_budget` / config / env inside the loop.** Bounds are frozen on
   the run row at start and read once into locals. (AQ-v0.5-4 / I7 — P0)
3. **DO NOT call any web-search backend other than SearXNG.** No `tavily`, `duckduckgo_search`,
   `googlesearch`, `serpapi`, or a raw Google/Bing endpoint — anywhere, including "fallback". The only
   web-search config entry point is `SEARXNG_URL`. (I9 — P0)
4. **DO NOT exceed `concurrency=3`.** All search + fetch fan-out passes through the single module
   `asyncio.Semaphore(CONCURRENCY=3)`. No second semaphore, no unbounded `gather`, no per-iteration
   pool resize. Changing the ceiling requires an architect-approved ADR amendment. (I7 — P0)
5. **DO NOT write the synthesis directly to `vault/wiki/`** or call `provider.generate()` /
   `write_wiki_page()` directly. The synthesis is source material; it is written to
   `vault/raw/sources/deep-research-<run_id>.md` and ingested via **`ingest_file`** (AQ-v0.5-3 / I1/I5).
6. **DO NOT hardcode a provider, model id, base_url, or API key** in `deep_research.py`. All inference
   is via the resolved `InferenceProvider.chat()` (operation `"ingest"`). No `isinstance` / provider-type
   branch. (I6)
7. **DO NOT leave a run in status `"running"` on loop fall-through.** `status` defaults to
   `"max_iter_reached"` and is overwritten only on a real exit; the terminal write is in a `finally`
   so an exception sets `"error"` + `error_message`, never a stuck `"running"`. (AC-F10-2b)
8. **DO NOT skip the sufficiency assessment before refining.** Assessment is inside the loop, before the
   refine decision, every round (CLAUDE.md §7). No "refine first, assess later".
9. **DO NOT add a per-source secondary loop** (e.g. "re-fetch until clean"). Fetch is single-pass per
   hit; failures are logged and dropped. The only loop in F10 is the bounded refinement loop. (I7)
10. **DO NOT introduce a new InferenceProvider ABC method** (`research()` / `assess()` / etc.). F10 rides
    the existing backend-neutral `chat()` seam; adding an abstract method churns all 3 backends and the
    I6 contract for no gain.
11. **DO NOT bump `data_version` from `deep_research.py` directly.** The re-entrant `ingest_file`
    performs the single `bump_version()` for the synthesis page (I1) — F10 must not double-bump.

---

## 6. Auto-ingest of the synthesis (AQ-v0.5-3 + I1/I5)

The synthesis is **not** a finished wiki page; it is a new **source document** the deep-research run
produced. It re-enters the normal pipeline:

```python
async def _ingest_synthesis(run_id, vault_id, synthesis_md: str) -> uuid.UUID:
    # 1. write to raw/sources/ with valid frontmatter (I5) — this is a SOURCE, type: source-equivalent
    rel = f"raw/sources/deep-research-{run_id}.md"
    abs_path = settings.vault_root / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(_frontmatter_wrap(synthesis_md, topic, run_id), encoding="utf-8")
    # 2. re-enter the SINGLE intake seam (ADR-0003). This runs the hash gate (I1),
    #    resolves the ingest provider, runs analyze→generate→validate (or CLI delegate),
    #    writes the wiki page(s), embeds, bumps data_version ONCE — all existing logic.
    result = await ingest_file(abs_path)        # ← AQ-v0.5-3: NOT provider.generate()
    return result.page_id
```

Consequences of routing through `ingest_file`:

- **I1** — the content-hash gate dedups (re-running deep research on the same topic that yields
  identical synthesis is a `skipped` no-op). `data_version` bumps exactly once, in `ingest_file`.
- **I5** — the writer (`write_wiki_page`) produces frontmatter-valid, `[[wikilink]]`-clean Obsidian
  pages. F10 adds zero new file-writing logic to `wiki/`.
- **I6** — the synthesis ingest uses whatever provider the vault's `"ingest"` config resolves to —
  consistent with the rest of the pipeline; deep research does not pick a backend.
- The raw source file means the watcher's later observe is a safe `skipped` no-op (the ADR-0020
  precedent).

The synthesize **prompt** (step 5) instructs the model to produce a well-structured markdown document
with `[[wikilinks]]` to likely vault concepts and inline source URLs — but the **authority** for
structure remains the downstream `analyze→generate` step, exactly as for any uploaded source.

---

## 7. Data model — migration 0009

Two tables. A child table is needed: one run fetches many sources, and AC-F10-6b explicitly requires a
per-source row with `url / title / fetched_content_md / relevance_score`.

> **Migration numbering correction.** Phase 2 (F10) lands **before** Phase 3 (F9 `review_items`).
> Migrations are sequential by *creation order*, so F10's tables take **0009** and F9's `review_items`
> becomes **0010** — superseding the tentative "0010 for F10" in `v0.5-architecture.md` §2.3/§2.2.
> The F9 phase plan (ADR-0025) inherits this correction.

### 7.1 `deep_research_runs` (the run ledger)

| Column | Type | Null | Notes |
|--------|------|------|-------|
| `id` | UUID PK | no | `gen_random_uuid()` |
| `vault_id` | String | no | scope (pages/edges/ingest_runs pattern; AQ-v0.5-6 — string, no `vaults` table) |
| `topic` | Text | no | the research topic |
| `status` | Text | no | `running` \| `converged` \| `max_iter_reached` \| `budget_exhausted` \| `error`; default `running` |
| `max_iter` | Integer | no | **frozen at start** (AQ-v0.5-4) — the cap actually applied |
| `token_budget` | Integer | no | **frozen at start** — the budget actually applied |
| `iterations_used` | Integer | no | rounds consumed (1..max_iter); default 0 |
| `queries_used` | JSONB | no | array of every query issued, per round (AC-F10-4c); default `[]` |
| `sources_fetched` | Integer | no | count of fetched candidate sources; default 0 |
| `converged` | Boolean | no | True iff status == `converged`; default false (audit convenience) |
| `total_cost_usd` | Numeric(10,4) | no | I7 ledger; 0.0000 for local/cli; default 0 |
| `synthesis_text` | Text | yes | the synthesized markdown; NULL until step 5 (AC-F10-4c) |
| `synthesis_page_id` | UUID FK→pages | yes | the `pages` row created by the re-entrant `ingest_file`; NULL until done |
| `started_at` | TIMESTAMPTZ | no | `now()` |
| `completed_at` | TIMESTAMPTZ | yes | NULL while `running` (AC-F10-4c mirrors ingest_runs alias rule) |
| `error_message` | Text | yes | populated only on status `error` |

Index: `(vault_id, started_at DESC)` for the paginated list (mirrors `ingest_runs`).

### 7.2 `deep_research_sources` (per-source child)

| Column | Type | Null | Notes |
|--------|------|------|-------|
| `id` | UUID PK | no | `gen_random_uuid()` |
| `run_id` | UUID FK→deep_research_runs.id | no | parent (ON DELETE CASCADE) |
| `url` | Text | no | the fetched source URL (from SearXNG hit) |
| `title` | Text | yes | hit title |
| `fetched_content_md` | Text | yes | extracted markdown (capped at `DEEP_RESEARCH_FETCH_MAX_CHARS`); NULL on fetch failure |
| `relevance_score` | Numeric(6,4) | yes | optional model/heuristic relevance; NULL if not scored |
| `iteration` | Integer | no | which round produced this source (audit); default 1 |
| `created_at` | TIMESTAMPTZ | no | `now()` |

Index: `(run_id)`.

### 7.3 SQLAlchemy + ER (D2) impact

Two new mapped classes in `models.py` (`DeepResearchRun`, `DeepResearchSource`) following the
`IngestRun` column-comment style. `make er` regenerates `docs/er/schema.mmd` to add both tables and the
`deep_research_runs ||--o{ deep_research_sources` relationship plus the
`deep_research_runs }o--|| pages` (synthesis_page_id) and the FK to nothing for `vault_id` (string scope,
no `vaults` table). AC-F10-6d: `make er` exits 0 and the diagram reflects both tables.

---

## 8. REST surface (D4) — mirrors `/ingest/runs`

All three documented in `docs/api/openapi.json` via `make openapi` (I8). These are exactly what F9
(Phase 3, ADR-0025) wires its **Deep-Research** review action to (AC-F10-5).

### 8.1 `POST /research/start` → **202**

```
Request body:  { "vault_id": str, "topic": str, "max_iter"?: int, "token_budget"?: int }
Response 202:  { "run_id": uuid }
```

- Validates `topic` non-empty and `vault_id`. `max_iter`/`token_budget` optional → env defaults; both
  **frozen onto the run row** before the background task starts (AQ-v0.5-4).
- Inserts the `deep_research_runs` row with `status="running"`, then schedules
  `run_deep_research(...)` as a background asyncio task (the ADR-0020 fire-and-poll pattern). Returns
  202 immediately — does NOT block on the loop.
- If `SEARXNG_URL` is unset → 503 config error (clean, no run row, no fake search). (I9)
- `max_iter` / `token_budget` are bounded by server-side `Query`/`Field` validators
  (`max_iter` 1..10, `token_budget` 1_000..1_000_000) so a caller cannot request an unbounded run (I7).

### 8.2 `GET /research/runs` → **200** (paginated)

```
Query:  ?vault_id=<str>&limit=<1..100=20>&offset=<>=0=0>
Response 200: { "items": [ { id, topic, status, iterations_used, sources_fetched,
                             total_cost_usd, started_at, completed_at } ... ],
                "total": int, "limit": int, "offset": int }
```

Started-at DESC, same pagination contract and `Query(ge=…, le=…)` 422 guards as `GET /ingest/runs`.

### 8.3 `GET /research/runs/{id}` → **200 / 404**

```
Response 200: { id, vault_id, topic, status, max_iter, token_budget, iterations_used,
                queries_used: [str], sources_fetched: int, total_cost_usd,
                synthesis_text: str | null, synthesis_page_id: uuid | null,
                sources: [ { url, title, relevance_score, iteration } ... ],
                started_at, completed_at, error_message }
404 when no run with {id}.
```

`synthesis_text` is null until step 5 completes (AC-F10-4c). The `sources` array is the
`deep_research_sources` children (without the full `fetched_content_md` blobs by default — a size
guard; full content is an opt-in `?include_content=true` follow-up if needed).

---

## 9. Sequence diagram (D3) — `docs/sequences/deep-research.mmd`

Authored as a Mermaid `sequenceDiagram` (handoff to tech-writer for final polish), it MUST show, per
AC-D3-DR-1: trigger → `_generate_queries` (InferenceProvider) → SearXNG multi-query (**note:
concurrency=3**) → fetch+parse → `_assess_sufficiency` (InferenceProvider) → `alt` branch
[sufficient: synthesize + `ingest_file`] / [insufficient: refine, **note: bounded by max_iter**] →
`total_cost_usd` logged. The participants mirror the loop (`run_deep_research`, `SearXNG`,
`InferenceProvider`, `ingest_file`). The `max_iter` cap and `concurrency=3` ceiling are explicit notes.

---

## 10. Invariant compliance statement

| Inv | How F10 guarantees it |
|-----|------------------------|
| **I7** | The refinement loop is `for iteration in range(1, max_iter + 1)` (structural cap). `token_budget` checked at the top of each round before spending. `concurrency=3` via a single module semaphore. Bounds frozen on the run row at start, never re-read. `status` defaults pessimistically to `max_iter_reached`; terminal write in `finally`. `total_cost_usd` accumulated + logged + $1 anomaly WARNING. The mandatory `max_iter_reached` test (AC-F10-2) is the gate. |
| **I9** | All web search via `ops/searxng.py` → `SEARXNG_URL` only. Static guard bans `tavily/ddg/duckduckgo/googlesearch/serpapi`. No fallback search engine. SearXNG, Qdrant, bge-m3 reused; nothing reinvented. |
| **I6** | Query-gen, assess, synthesize all via the resolved `InferenceProvider.chat()` (operation `"ingest"`). No backend / model / key hardcoded. No `isinstance`/provider-type branch. No new ABC method. |
| **I1** | Synthesis re-enters `ingest_file` (ADR-0003): hash-gated, incremental, single `data_version` bump in the seam. F10 never writes `pages`/Qdrant directly and never double-bumps. |
| **I5** | Synthesis is a `raw/sources/` document; the wiki page is produced by the existing frontmatter-valid writer. `wiki/` stays a valid Obsidian vault. |
| **I8** | D2 (`make er` — two tables, migration 0009), D3 (`deep-research.mmd`), D4 (`make openapi` — three endpoints). All regenerated, zero drift. |

No invariant is traded for convenience.

---

## 11. Consequences

**Positive:**

- F10's boundedness is provable by reading the loop header — a single `range(1, max_iter+1)` line plus
  one top-of-round budget gate. This is the strongest possible I7 posture for the most loop-intensive
  feature.
- Zero new InferenceProvider surface: F10 rides `chat()`, so all three backends (Local/API/CLI) work
  the moment Phase 1's `chat()` implementations land. I6 is untouched.
- Synthesis-through-`ingest_file` means F10 inherits dedup, incrementality, Obsidian-validity, and the
  cost ledger for free — no parallel write path to keep consistent.
- The run + sources tables give F9 (Phase 3) a clean `run_id` to reference and a full audit trail.

**Negative / accepted trade-offs:**

- **Source-extraction quality is basic in Phase 2.** Backend HTML→markdown reduction is cruder than the
  clipper's Readability. Accepted: the synthesis step + downstream `analyze` tolerate noisy input; a
  richer extractor (shared with F12's `unstructured`) is a measured follow-up, not a guess.
- **Two inference round-trips per iteration** (generate + assess) plus one synthesize. This is the cost
  of "assess before refine" (CLAUDE.md §7). The `token_budget` gate bounds it; the default 100k budget
  comfortably covers 3 iterations of modest fetches.
- **`relevance_score` is optional/best-effort** in Phase 2 (NULL allowed). A scored re-ranking of
  fetched sources is reserved; Phase 2 collects all fetched sources up to the per-source char cap.
- **No streaming progress to the UI mid-run.** The UI polls `GET /research/runs/{id}` (the ADR-0020
  poll pattern, AC-F10-8). A live progress stream is out of scope for Phase 2.

---

## 12. Status of resolved AQs

- **AQ-v0.5-3 — RESOLVED:** synthesis flows through the full `ingest_file` seam (§6), **not** a direct
  `generate()`. The synthesis is source material.
- **AQ-v0.5-4 — RESOLVED:** `max_iter` configurable (`POST` body → env default **3**), `token_budget`
  default **100_000**, `max_queries_per_iter=5`, **`concurrency=3` hardcoded**; all frozen on the
  `deep_research_runs` row at start, never re-read mid-loop (§3).

---

> **Handoffs:** this ADR → tech-writer (formatting + README row + `deep-research.mmd` polish).
> Interface contracts (§2.2/§2.3, §4) → backend-engineer (`ops/deep_research.py`, `ops/searxng.py`,
> migration 0009, REST handlers). D3 stub (§9) → tech-writer. Phase-2 verdict → orchestrator.
