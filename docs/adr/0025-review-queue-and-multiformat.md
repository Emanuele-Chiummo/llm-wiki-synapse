# ADR-0025 — HITL Review Queue + Multi-format ingest (F9 + F12)

- **Status:** Accepted
- **Date:** 2026-06-29
- **Sprint:** v0.5 (M5 Phase 3)
- **Features:** F9 (Async HITL review queue) · F12 (Multi-format ingest)
- **Supersedes the F9/F12 interface stubs in:** `docs/sprints/v0.5-architecture.md` §2.2 / §2.4 / §6.2 / §6.4 (this ADR is the detailed design; the stubs stand as the coherence map)
- **Resolves:** AQ-v0.5-6 (`review_items.vault_id` is the existing `vault_id` String, no `vaults` table; `review_items` is Alembic **0010** — F10 took 0009)
- **Invariants owned:** **I7** (HEADLINE — the F9 pre-gen query call is bounded to exactly 1 call/item) · **I1** (F12 stays incremental via the hash gate) · **I5** (extracted pages are Obsidian-valid; binary never reaches `wiki/`) · **I6** (pre-gen queries via the resolved `InferenceProvider`, no hardcoded backend) · **I9** (well-known extractor libs, no reinvention) · **I4** (review list virtualized > 50)
- **Author:** solution-architect

---

## 1. Context

Phase 3 is the broadest M5 phase: it lands two independent features that share only the
sprint gate. They are designed together here because both reuse existing seams and both have
sharp invariant edges that must be locked before engineers code.

**F9 — HITL review queue.** Karpathy's K8 principle is "human curates, LLM maintains." After
an ingest writes a `wiki/` page, F9 surfaces it (and any AI-proposed follow-up questions) in a
**Review** nav section so the human can Approve / Skip / Deep-Research. The queue is **advisory**:
the page is already on disk (AC-F9-2/6); the queue never blocks ingest and never re-triggers it.
The danger is the **pre-generated query** step — an inference call per item. Left unbounded it is
exactly the I7 failure class. It is bounded here to **exactly one** `InferenceProvider` call per
item, with a timeout, degrading to a null query on failure.

**F12 — Multi-format ingest.** Today only `.md/.txt/.markdown` enter the vault (the `app.upload`
allow-list, mirrored by the watcher). F12 accepts PDF/DOCX/PPTX/XLSX by extracting their text to a
companion markdown file and letting the **existing** ingest pipeline take over. The danger is two
seams colliding (upload → watcher → `ingest_file`) and producing a double-ingest, or a binary file
reaching the indexer. Both are designed out below.

### 1.1 Ground truth consumed

- **`app.upload`** (`safe_source_name` / `resolve_under_sources`) gates `POST /ingest/upload`. Its
  `_ALLOWED_EXTENSIONS = {".md", ".txt", ".markdown"}` is the **single** source of truth for what the
  watcher ingests — `app.watcher._is_text_file` imports the exact same frozenset. **This coupling is
  the linchpin of the F12 design** (§4.3): binary extensions stay OUT of that set, so the watcher
  ignores the binary and only ingests the companion `.extracted.md`.
- **`POST /ingest/upload`** (M4-EXT, ADR-0020) writes to `vault/raw/sources/` and returns **202**;
  the **watcher** ingests asynchronously. F12 must run extraction *synchronously on upload* (before
  202) so the companion markdown exists when the watcher fires — never inside the watcher.
- **`ingest/orchestrator.py` `ingest_file(path)`** — the single intake seam (ADR-0003). Extracted text
  re-enters here unchanged; the mtime/hash gate (ADR-0001) dedups regardless of source type (I1).
- **`run_ingest_pipeline()`** (orchestrator) is where each `WikiPage` is written via `write_wiki_page`.
  F9's `enqueue_review()` is called **fire-and-forget** from the orchestrator's post-write step.
- **`resolve_provider_config("ingest", vault_id)`** (`provider_config_service.py`) resolves the provider
  row by operation precedence and raises `ConfigNotFoundError` when none is configured. F9's query-gen
  uses the resolved **`"ingest"`** provider via `InferenceProvider.chat()` — the same backend-neutral
  text-in/text-out seam F10 uses (no new ABC method, I6).
- **`POST /research/start`** (F10, ADR-0024) — F9's Deep-Research action calls this with the item's
  `pre_generated_query` (or the page topic) and stores the returned `run_id` on the review row (AC-F10-5).
- **`graph/engine.py` / `edges` table** — the post-ingest consistency scan (CLAUDE.md §7) may **read**
  the `edges` table to find graph-related pages. It **never** triggers FA2 (I2). Phase 3 keeps the
  generator scope minimal (see §3.4): consistency/contradiction flagging is a **reserved, bounded**
  extension; the shipped generator produces follow-up `suggested_query` items only.

---

## 2. Decision summary

1. **F9 stores one new table** `review_items` (Alembic **0010**), scoped by the existing `vault_id`
   String (AQ-v0.5-6 — no `vaults` table). `page_id` is a real nullable FK → `pages.id`.
2. **Items come from exactly one source in Phase 3:** the orchestrator's post-write hook enqueues one
   `new_page` review item per generated wiki page, and **at most one** bounded `InferenceProvider.chat()`
   call generates 1–3 follow-up `suggested_query` strings stored on that row. No second call, no loop (I7).
3. **F9 REST:** `GET /review/queue` (paginated, `vault_id` filter), `POST /review/queue/{id}/approve`,
   `POST /review/queue/{id}/skip`, `POST /review/queue/{id}/deep-research`. The deep-research action
   delegates to `POST /research/start` (F10) and stores `run_id` + `deep_research_run_id` on the row.
4. **F12 extraction lives in one module** `ingest/extract.py` with a single dispatch `extract_text(path)
   -> str` (no LLM, no loop). Format libs (`pypdf`, `python-docx`, `python-pptx`, `openpyxl`) are
   imported **only** there (static guard, AC-F12-7).
5. **F12 plug-in point:** `POST /ingest/upload` widens its allow-list for binary types; on a binary it
   (a) writes the original binary to `raw/sources/`, (b) **synchronously** extracts → writes
   `<stem>.extracted.md` next to it, (c) returns 202. The **watcher ingests only the `.extracted.md`**
   (the binary extension is not in `_ALLOWED_EXTENSIONS`). One ingest, one hash gate (I1), Obsidian-valid (I5).
6. **`unstructured` is NOT added in M5.** Images/AV are explicit placeholder stubs (out of scope, §4.5).
   This keeps the dependency bundle lean (I9) — decision locked here, narrowing the scope-doc list.

---

## 3. F9 — HITL Review Queue

### 3.1 `review_items` table (Alembic 0010)

New SQLAlchemy model `ReviewItem` in `backend/app/models.py`. DDL (Postgres; SQLite variant for unit
tests follows the `deep_research_runs` pattern — `UUID(as_uuid=True).with_variant(String(36), "sqlite")`,
`JSONB().with_variant(JSON(), "sqlite")`):

| Column | Type | Null | Notes |
|--------|------|------|-------|
| `id` | UUID PK | no | `gen_random_uuid()` server default |
| `vault_id` | String | no | **The existing `vault_id` String** (AQ-v0.5-6); matches `pages`/`edges`/`conversations`. No FK — there is no `vaults` table. |
| `page_id` | UUID FK → `pages.id` | **yes** | The wiki page this item reviews; NULL for page-less items (e.g. a future gap item not tied to one page). |
| `item_type` | Text (enum-by-convention) | no | `new_page` \| `update_page` \| `deep_research_candidate`. CHECK constraint enumerates values (matches scope-doc AC-F9-1; `suggested_query`/`gap`/`contradiction` are **payload sub-kinds**, not new column values — see §3.4). |
| `status` | Text (enum-by-convention) | no | `pending` \| `approved` \| `skipped` \| `deep_researched`. Defaults `pending` (server default). CHECK constraint. |
| `pre_generated_query` | Text | yes | Newline-separated 1–3 questions from the bounded gen call; **NULL** when the call failed/timed out or was not configured (AC-F9-4). |
| `deep_research_run_id` | UUID FK → `deep_research_runs.id` | yes | Set when the Deep-Research action fired (AC-F10-5); NULL otherwise. |
| `created_at` | TIMESTAMP(tz) | no | `func.now()` |
| `reviewed_at` | TIMESTAMP(tz) | yes | Set on approve/skip/deep-research; NULL while `pending`. |
| `reviewed_by` | Text | yes | Free-text actor (e.g. `"web-ui"`); NULL while `pending`. M5 is single-operator, so this is audit-only. |

**Index:** `ix_review_items_vault_status_created` on `(vault_id, status, created_at)` — the
paginated pending-queue read (`WHERE vault_id=? AND status='pending' ORDER BY created_at`). No
partial-unique constraint is needed (a page may legitimately appear multiple times across re-ingests;
the queue is an event log, not a per-page singleton).

> **Naming reconciliation with the scope doc.** AC-F9-1 lists `vault_id (uuid FK)`. AQ-v0.5-6 already
> overrode this to the **String `vault_id`** with no `vaults` table; this ADR is the authority. The
> scope-doc `item_type` enum (`new_page`/`update_page`/`deep_research_candidate`) is honored verbatim;
> the §intro mention of `suggested_query`/`gap`/`contradiction` in the design brief maps to **payload
> sub-kinds inside `pre_generated_query`**, not extra column values — see §3.4.

**ER / D2 impact.** `make er` regenerates `docs/er/schema.mmd` with the `review_items` entity and its two
FKs (`page_id` → `pages`, `deep_research_run_id` → `deep_research_runs`). Zero-drift gate (I8, EC-M5-17).

### 3.2 Module `ops/review.py` — interface contract

```python
# backend/app/ops/review.py  — F9 (ADR-0025). No new ingest logic; advisory queue only.

async def enqueue_review(
    *,
    vault_id: str,
    page_id: uuid.UUID | None,
    item_type: Literal["new_page", "update_page", "deep_research_candidate"],
    pre_generated_query: str | None = None,
) -> ReviewItem:
    """Insert one pending review_items row. Pure DB write; never calls a provider itself.
    Idempotency is NOT required — the queue is an event log (§3.1)."""

async def generate_review_queries(
    *, vault_id: str, page_title: str, page_excerpt: str
) -> str | None:
    """Make EXACTLY ONE InferenceProvider.chat() call (operation='ingest', I6) to produce
    1–3 follow-up research questions, returned newline-separated. BOUNDED (I7):
      - one call, no loop, no refine;
      - wrapped in asyncio.wait_for(..., REVIEW_QUERY_TIMEOUT_SECONDS);
      - token_budget from the resolved provider row (or REVIEW_QUERY_TOKEN_BUDGET default);
      - on ConfigNotFoundError / timeout / any provider error → return None (item still enqueued).
    Cost is pushed through the UsageAccumulator and logged like any provider call (I7)."""

# REST handlers (thin; live in main.py or an ops/review router, calling the above):
async def list_queue(vault_id: str, *, limit: int, offset: int) -> ReviewQueuePage
async def approve(item_id: uuid.UUID) -> ReviewItem
async def skip(item_id: uuid.UUID) -> ReviewItem
async def deep_research(item_id: uuid.UUID) -> ReviewItem   # → POST /research/start, store run_id
```

### 3.3 Where items come from (LOCKED) — the bounded post-ingest generator

The **only** producer in Phase 3 is the orchestrator's existing post-write step. In
`run_ingest_pipeline` (orchestrator), after the orchestrated branch finishes writing pages
(`write_wiki_page` + `_update_overview`), a **fire-and-forget** hook runs:

```
for each WikiPage written:
    # ONE bounded provider call per page (I7); failure → query=None, item still enqueued
    query = await generate_review_queries(vault_id, page.title, excerpt(page.content))
    await enqueue_review(vault_id, page_id, item_type="new_page", pre_generated_query=query)
```

**Hard rules (the I7 + AC-F9-2 contract):**

1. The hook is wrapped in `try/except` and **NEVER fails the ingest** (AC-F9-2). The page is already on
   disk; the queue is advisory. A generator exception logs a WARNING and enqueues the item with
   `pre_generated_query=NULL`.
2. **Exactly one** `generate_review_queries` call per page. No second call, no loop, no retry. The single
   call is itself bounded by a timeout + token_budget (§3.2). This is the I7 surface for F9.
3. The hook is **non-blocking to the ingest critical path**: it runs after the page write and its cost is
   logged but it does not gate `IngestRunResult`. (Implementation may `asyncio.create_task` it or run it
   inline after the run row is written — engineer's choice — but it must not raise into the ingest caller.)
4. It is attached to the **orchestrated** branch only. The **delegated (CLI)** branch writes pages through
   the MCP `write_page` tool and the orchestrator does not enumerate them; enqueue for the delegated path
   is a **reserved, out-of-Phase-3 follow-up** (recorded in §7 risks). M5 EC-M5-8 ("review queue populated
   on ingest") is satisfied by the orchestrated path, which is the default dev provider (Ollama/API).

### 3.4 Consistency/contradiction scan — reserved and bounded

CLAUDE.md §7 lists a "consistency/contradiction post-ingest" loop that "scans related pages via the graph
→ flags to review (F9), bounded." Phase 3 **reserves** this: the shipped generator produces
`suggested_query` follow-ups only. If a consistency scan is added later it MUST:
- read the `edges` table for graph-related pages (I2 — never call FA2);
- be bounded by `REVIEW_SCAN_MAX_ITEMS` (max items enqueued per scan) **and** the same single-call
  token_budget (I7);
- enqueue `deep_research_candidate` / page-less items with the flagged context in `pre_generated_query`.

This reservation is explicit so a future PR cannot smuggle an unbounded graph walk in under F9.

### 3.5 F9 REST surface (D4)

| Method + path | Body / params | Success | Notes |
|---------------|---------------|---------|-------|
| `GET /review/queue` | `?vault_id&limit&offset` (limit default 50, max 200 — I7 cap on the page) | 200 `{items:[…], total, limit, offset}` | Pending items, `created_at` order. Mirrors `/ingest/runs` paging. |
| `POST /review/queue/{id}/approve` | — | 200 `ReviewItem` | `status=approved`, `reviewed_at=now()`. Human confirmation only; does **not** re-trigger ingest (AC-F9-6). |
| `POST /review/queue/{id}/skip` | — | 200 `ReviewItem` | `status=skipped`, `reviewed_at=now()`. |
| `POST /review/queue/{id}/deep-research` | — | **202** `{review_item_id, run_id}` | `status=deep_researched`; calls `POST /research/start` with `pre_generated_query` (first line) or the page topic when query is NULL; stores `deep_research_run_id`. 503 if `SEARXNG_URL` unset (inherits F10's guard). |
| (all) | unknown `id` | 404 | |

`ReviewItem` JSON projection: `{id, vault_id, page_id, page_title?, item_type, status,
pre_generated_query, deep_research_run_id, created_at, reviewed_at}`. `page_title` is a convenience
join from `pages` for the UI list (AC-F9-5). All four documented in `openapi.json` (`make openapi`, I8).

### 3.6 F9 frontend (AC-F9-5/-7)

The **Review** nav section (its `Section` union member + `SectionRouter` branch were retained per
ADR-0021's M5-item-removal pattern) is activated: a list of pending items showing page title / item
type / pre-generated query / Approve·Skip·Deep-Research buttons. The list is **virtualized with TanStack
Virtual when > 50 items** (I4). F9 is a **separate** section from the M4 Ingest Activity View — no
Approve/Skip/Deep-Research is added to the Sources/Ingest section (AC-F9-7, the §10 boundary).

---

## 4. F12 — Multi-format ingest

### 4.1 Module `ingest/extract.py` — interface contract

```python
# backend/app/ingest/extract.py  — F12 (ADR-0025). SOLE home of format libs (static guard, AC-F12-7).

# Extension → extractor dispatch. No LLM, no loop, single pass per file (I7 n/a — stateless).
EXTRACTABLE_BINARY_EXTENSIONS: frozenset[str] = frozenset(
    {".pdf", ".docx", ".pptx", ".xlsx"}
)
# Placeholder-only (no transcription/OCR in M5 — §4.5):
PLACEHOLDER_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp3", ".mp4", ".wav", ".m4a"}
)

def extract_text(file_path: str | Path) -> str:
    """Dispatch on the lower-cased suffix → plain text.
      .pdf  → pypdf (page text joined; embedded images skipped with a log warning, AC-F12-1)
      .docx → python-docx (paragraph text; basic structure)
      .pptx → python-pptx (slide text; one logical doc per presentation)
      .xlsx → openpyxl (sheet name + rows rendered as a GFM markdown table)
      image/AV (PLACEHOLDER_EXTENSIONS) → a one-line placeholder string (§4.5)
      anything else → raise UnsupportedFormatError  (caller maps to HTTP 415)
    Output is capped at EXTRACT_MAX_CHARS (config, default ~2_000_000) to bound a pathological file."""

class UnsupportedFormatError(ValueError): ...
```

`extract_text` is **pure** (path in, text out) and makes **no** LLM/provider call (I6/I9, AC-F12-1, the
§10 "extraction only, not new inference" boundary). The static guard (AC-F12-7) asserts no `pypdf`/`docx`/
`pptx`/`openpyxl` import exists outside this module.

### 4.2 Where it plugs in — `POST /ingest/upload`

The upload allow-list widens. `app.upload.safe_source_name` is extended so its extension gate accepts the
binary set in addition to text:

```
_ALLOWED_EXTENSIONS        = {".md", ".txt", ".markdown"}                 # watcher ingests THESE
_EXTRACTABLE_EXTENSIONS    = {".pdf", ".docx", ".pptx", ".xlsx"}          # extracted on upload
_PLACEHOLDER_EXTENSIONS    = {".png",".jpg",".jpeg",".gif",".webp",
                              ".mp3",".mp4",".wav",".m4a"}                 # placeholder on upload
_UPLOAD_ACCEPTED           = _ALLOWED_EXTENSIONS | _EXTRACTABLE_EXTENSIONS | _PLACEHOLDER_EXTENSIONS
# 415 iff suffix not in _UPLOAD_ACCEPTED (AC-F12-2). _ALLOWED_EXTENSIONS is UNCHANGED so the
# watcher's import of it still means "text companions only".
```

Upload handler flow for a binary (`POST /ingest/upload`, still returns **202**):

```
1. safe_source_name(filename) — now allows binary ext; 415 only for truly unknown types.
2. stream body to temp with the MAX_UPLOAD_BYTES cap (413) — unchanged.
3. atomic move → vault/raw/sources/<name>.<ext>            (the ORIGINAL binary, preserved; I5/K1)
4. if suffix in _EXTRACTABLE | _PLACEHOLDER:
       text = extract_text(dst)            # SYNCHRONOUS, on the upload path (NOT in the watcher)
       atomic-write text → vault/raw/sources/<stem>.extracted.md   (Obsidian-valid; see §4.4)
   else (.md/.txt/.markdown): no companion — the file is already watcher-ingestable.
5. return 202 {file_path, status:"queued", overwritten}
```

The **watcher** then fires on the new `.extracted.md` (which IS in `_ALLOWED_EXTENSIONS`) and runs
`ingest_file` exactly once. It **ignores the binary** (`.pdf`/etc. are not in `_is_text_file`). Net:
**one** ingest, **one** hash gate (I1), **one** wiki page produced by the unchanged pipeline (I6).

### 4.3 Why companion-on-upload (not extract-in-watcher)

Two seams already exist: upload writes + watcher ingests. The temptation is to teach the watcher to
extract. **Rejected** — that would put binary-parsing on the ingest hot path and inside the watchdog
thread, and the watcher deliberately knows nothing about formats. Extracting **synchronously on the
upload request**, before the 202, keeps:
- the watcher unchanged (it only ever sees text it can ingest);
- a single ingest path (`ingest_file`), no double-ingest, hash gate intact (I1);
- the binary as an immutable `raw/` artifact (K1 3-layer rule, AC-F12-4).

Files arriving via the **scheduled importer (Feature S)** or copied directly into `raw/sources/` that are
binary will be **silently ignored by the watcher** (no companion exists). That is the correct,
conservative behavior for M5: extraction is an **upload-time** capability. Auto-extraction of binaries
that appear in `raw/sources/` by other means is a reserved follow-up (§7) — it must not be smuggled into
the watcher without an ADR.

### 4.4 Companion `.extracted.md` is Obsidian-valid (I5)

The companion is written with minimal valid YAML frontmatter so the pipeline's frontmatter parser (K6)
and the Obsidian-compat check (`test_obsidian_check.py`) stay green:

```
---
type: source
title: <original filename stem>
sources: ["raw/sources/<name>.<ext>"]
---

<extracted text>
```

`sources[]` points back at the binary (F3 traceability). `wiki/` is written **only** by the ingest
pipeline (AC-F12-4, EC-M5-11). The companion lives in `raw/sources/` alongside the binary — **not** in
`raw/assets/` and **not** in `wiki/`.

### 4.5 Images / AV — placeholder only (out of scope, locked)

Per AC-F12-1 and the §10 boundary, images and AV are **not transcribed/OCR'd in M5**. For a placeholder
extension the companion body is a single line — `"Image file: no text extracted (transcription out of
scope in this release)."` / `"AV file: transcript not available in this release."` — so the upload is
accepted (not 415) and produces a traceable stub page, while no transcription machinery is added. This
**confirms** the scope-doc intent.

### 4.6 New dependencies (pinned, lean — I9)

Added to `backend/pyproject.toml`, **pinned with floors**:

| Package | Floor | Why | Bundle note |
|---------|-------|-----|-------------|
| `pypdf` | `>=4.2,<6` | PDF text (pure-Python, no native build) | tiny, pure-Python |
| `python-docx` | `>=1.1,<2` | DOCX paragraphs | small; pulls `lxml` |
| `python-pptx` | `>=0.6.23,<1.1` | PPTX slide text | small; shares `lxml`/`Pillow`(optional) |
| `openpyxl` | `>=3.1,<4` | XLSX cells → markdown table | small; pure-Python |

**`unstructured` is deliberately NOT added** (the scope doc listed it for image OCR). It is a heavy
dependency tree (pulls model/runtime deps) for a capability M5 explicitly defers (§4.5). Adding it for
placeholder-only image handling violates I9 ("minimize deps / do not over-add"). If real OCR is scoped in
M6, `unstructured` (or a lighter OCR path) is introduced **then**, behind the same `extract.py` dispatch,
under its own ADR. This is the one place this ADR narrows the scope-doc dependency list — recorded as a
decision, not a silent omission.

The Docker image rebuilds cleanly with the four pure-Python/lxml libs (AC-F12-5). All four are pinned.

---

## 5. Do-NOT list (rejection triggers)

A PR touching F9 or F12 is **rejected on review** if it does any of the following:

**F9**

1. **DO NOT** make more than one `InferenceProvider` call per review item, or put the query-gen in any
   loop / retry. Exactly one bounded call; failure → `pre_generated_query=NULL` (I7, AC-F9-4).
2. **DO NOT** let the post-ingest enqueue hook raise into or block the ingest path. The page is already
   written; the queue is advisory (AC-F9-2/6). `try/except`, log, continue.
3. **DO NOT** add a `vaults` table or make `review_items.vault_id` a UUID FK. It is the existing
   `vault_id` String (AQ-v0.5-6).
4. **DO NOT** hardcode a backend or model for query-gen. Resolve via `resolve_provider_config("ingest")`;
   route by capabilities, never by isinstance/provider_type (I6).
5. **DO NOT** add Approve/Skip/Deep-Research actions to the M4 Ingest Activity View (Sources section). F9
   is a separate Review section (AC-F9-7, §10).
6. **DO NOT** re-trigger ingest on Approve, or re-scan the vault on any review action. Approve is a status
   write only (AC-F9-6, I1).
7. **DO NOT** introduce a consistency/contradiction graph walk that calls FA2 or is unbounded. Reserved
   and bounded only (§3.4, I2/I7).
8. **DO NOT** return an unbounded `GET /review/queue` page — `limit` is capped (default 50, max 200).
9. **DO NOT** skip TanStack virtualization for the review list when it can exceed 50 rows (I4).

**F12**

10. **DO NOT** import `pypdf`/`docx`/`pptx`/`openpyxl` anywhere outside `ingest/extract.py` (static
    guard, AC-F12-7).
11. **DO NOT** call any LLM/provider during extraction. `extract_text` is pure text-in/text-out (AC-F12-1,
    I6, §10).
12. **DO NOT** extract inside the watcher or make the watcher format-aware. Extraction is synchronous on
    the upload request (§4.3).
13. **DO NOT** add the binary extensions to `app.upload._ALLOWED_EXTENSIONS` (the watcher imports it).
    Binaries go in a separate accepted-but-not-watched set; only `.extracted.md` is watcher-ingestable.
14. **DO NOT** write extracted output to `vault/wiki/` directly, or bypass `ingest_file`. Companion goes
    to `raw/sources/`; the pipeline writes `wiki/` (I1/I5, AC-F12-4).
15. **DO NOT** delete or mutate the original binary after extraction. It is an immutable `raw/` artifact
    (K1, AC-F12-4).
16. **DO NOT** add `unstructured` (or any heavy OCR/model dep) in M5. Images/AV are placeholder-only (§4.5,
    I9).
17. **DO NOT** bypass the `MAX_UPLOAD_BYTES` cap or the `EXTRACT_MAX_CHARS` output cap (I7 — bound a
    pathological file).

---

## 6. Invariant compliance statement (Phase 3)

| Inv | How F9 + F12 guarantee it |
|-----|---------------------------|
| **I1** | F12 reuses `ingest_file`'s mtime/hash gate; re-uploading the same file is a `skipped` no-op (AC-F12-3). No review action re-scans the vault; Approve is a status write. |
| **I2** | F9 query-gen does not touch the graph. The reserved consistency scan reads `edges` only — never FA2 (§3.4). |
| **I5** | F12 companion has valid YAML frontmatter; binary stays in `raw/sources/`; `wiki/` written only by the pipeline. `test_obsidian_check.py` 15/15 stays green (EC-M5-14). |
| **I6** | F9 query-gen resolves the provider via `resolve_provider_config("ingest")` and rides `InferenceProvider.chat()` — no hardcoded backend. F12 extractors make zero inference calls. |
| **I7** | F9 query-gen = exactly one bounded call (timeout + token_budget) per item; failure → NULL; no loop. `GET /review/queue` `limit` capped. F12 extraction is single-pass with an output cap; no loop. |
| **I8** | D2 regenerated (`review_items`). D4 regenerated (4 review endpoints + widened upload). Zero drift (EC-M5-17). |
| **I9** | F12 uses well-known pure-Python extractor libs; `unstructured`/heavy OCR deliberately deferred. No reinvention. |
| **I4** | Review list virtualized > 50 rows (AC-F9-5). |

No invariant is traded for convenience in Phase 3.

---

## 7. Flagged tensions & risks

1. **Delegated (CLI) ingest does not enqueue review items in Phase 3.** The orchestrator does not
   enumerate pages written by the CLI agent through MCP `write_page`, so the post-write hook attaches to
   the **orchestrated** branch only (§3.3.4). EC-M5-8 is met because the default dev provider is
   orchestrated (Ollama/API). Enqueue-from-delegated (e.g. the MCP `write_page` tool enqueuing on write)
   is a **reserved follow-up**, not an invariant violation. Recorded so it is a conscious gap, not a
   silent one.
2. **Query-gen cost on every ingested page.** One extra provider call per generated page adds latency and
   (for API providers) cost. Mitigated by the single-call bound + timeout + the existing $1 anomaly
   WARNING via the accumulator. The call is fire-and-forget off the critical path (§3.3.3).
3. **Binaries arriving outside upload are not auto-extracted** (Feature S / direct copy). Conservative for
   M5 (§4.3): extraction is an upload-time capability. Auto-extraction in `raw/sources/` is a reserved
   follow-up behind a future ADR — it must not be added to the watcher silently.
4. **`unstructured` deferral narrows the scope doc.** AC-F12-1 mentions `unstructured` for image OCR; this
   ADR defers it (§4.6) to keep the bundle lean (I9). Images/AV ship as placeholders, satisfying the
   accept-not-415 behavior without the heavy dep. PM/tech-writer note: the scope doc's `unstructured`
   line is read as "image handling exists" (placeholder), not "OCR ships in M5."
5. **`review_items` is an event log, not a per-page singleton.** Re-ingesting a page enqueues another
   item. Accepted: the queue reflects ingest events; the human disposes of duplicates by skipping. A
   dedupe/coalesce policy is a post-M5 refinement, not an M5 requirement.

---

## 8. Sign-off

**APPROVED to implement (Phase 3).** The `review_items` table (Alembic **0010**, String `vault_id`,
nullable FKs to `pages` and `deep_research_runs`), the **exactly-one-bounded-call** post-ingest query
generator (I7), the four `/review/queue` endpoints delegating Deep-Research to F10, the pure
`extract_text` dispatch in `ingest/extract.py` (four pure-Python libs, `unstructured` deferred), and the
**companion-`.extracted.md`-on-upload / watcher-ingests-the-companion** plug-in (one ingest, hash gate
intact, binary never reaches `wiki/`) together satisfy I1, I2, I4, I5, I6, I7, I8, I9. AQ-v0.5-6 is
honored. The Do-NOT list (§5) is the rejection gate.

Conditions on the Phase 3 gate (architect review, EC-M5-19):
1. F9 query-gen makes **exactly one** provider call per item; a forced timeout/error leaves the item
   enqueued with `pre_generated_query=NULL` and never fails the ingest (test required).
2. The post-ingest hook cannot raise into the ingest caller (try/except proven by test).
3. `review_items.vault_id` is String; no `vaults` table; migration is **0010**; `make er` shows the
   entity with both FKs, zero drift.
4. Static guard: no `pypdf`/`docx`/`pptx`/`openpyxl` import outside `ingest/extract.py`; no `unstructured`
   added; no LLM call in `extract.py`.
5. Uploading a binary writes the original to `raw/sources/`, produces one `.extracted.md`, and the
   watcher ingests **only** the companion (one `pages` row, one Qdrant point); re-upload is `skipped`
   (I1). `_ALLOWED_EXTENSIONS` is unchanged.
6. D4 regenerated with the 4 review endpoints + widened upload accept list; zero drift (I8).

> Handoff: ADR-0025 → tech-writer (formatting + README row). Interface contracts (§3.2, §4.1) →
> backend-engineer (`ops/review.py`, `ingest/extract.py`, the orchestrator hook, the upload widening,
> migration 0010) + frontend-engineer (Review section list + actions). C4: no new container/component —
> `review.py` and `extract.py` are components inside the existing FastAPI service; no D1 topology change.
> Phase verdict → orchestrator.
