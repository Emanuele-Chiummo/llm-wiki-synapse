# ADR-0030 — Embeddings on/off toggle with lexical degrade (global env flag, Postgres keyword fallback)

- **Status:** Accepted (owner decided 2026-06-29: global env `EMBEDDINGS_ENABLED`; per-vault scope deferred) — implemented
- **Date:** 2026-06-29
- **Sprint:** v0.5 (Feature B — embeddings toggle + lexical degrade)
- **Feature:** F5 (4-phase retrieval) · F17-adjacent (embedding is part of the AI data plane) · builds on ADR-0004 (EMBEDDING_DIM startup validation) and ADR-0022 (4-phase retrieval)
- **Invariants owned:** I1 (no full re-scan on toggle) · I9 (reuse Postgres; no new search engine) · I7 (degrade stays a single bounded pass)
- **Author:** solution-architect
- **Implementers:** ai-agent-engineer (embedding skip in ingest; embeddings-disabled embed contract) · backend-engineer (retrieval lexical phase + `/search` + startup guard + config) · frontend-engineer (Settings → Embeddings shows on/off state) · tech-writer (D4 note; D6 user guide)

---

## 1. Context

`backend/app/embeddings.py` always uses `HttpEmbeddingClient`; there is **no enable/disable
flag**. Embeddings are called unconditionally in two hot paths:

- **Ingest** — `upsert_vector()` (`orchestrator.py:825`) embeds every page and upserts to
  Qdrant.
- **Retrieval** — `retrieve()` Phase 1 (`rag/retrieval.py:217 _phase1_vector_search`) embeds
  the query for the dense Qdrant search; `/search` (main.py:1290) and `/chat` both call
  `retrieve()`. `search_wiki` (MCP) also embeds.

Startup (`_validate_embedding_and_collection`, main.py:2944) **fails fast** if the embedding
service is unreachable. So today a vault with no embedding backend cannot start, and
retrieval/search hard-depend on vectors.

The owner wants a flag to **disable embeddings**: ingest skips vectorization, and `/search` +
the 4-phase pipeline (F5) **degrade to lexical/keyword-only** instead of erroring. The Page
table already has `title` and `page_type` columns and source files on disk; Postgres is the
obvious lexical backend (I9 — no new engine).

---

## 2. Decision

### 2.1 Scope — **global env flag `EMBEDDINGS_ENABLED`** (recommended; per-vault deferred)

New env var **`EMBEDDINGS_ENABLED`** (bool, default **`true`**), read in `config.py`.

**Rationale for global over per-vault:**
- Embeddings config (`EMBEDDING_URL/MODEL/DIM`) is **already global env-only** — there is no
  per-vault embedding config today. A per-vault toggle would be the *first* per-vault embedding
  setting and would imply per-vault `provider_config`-style rows, a resolver, and a UI — scope
  far beyond "a flag to disable embeddings."
- v0.x is effectively single-vault (`vault_id="default"`). A global flag matches the real
  deployment shape and is the smaller, correct change.
- The `provider_config` precedence machinery (ADR-0008) exists for **inference** provider
  selection, not the embedding data plane. Reusing it for embeddings would conflate two
  separate abstractions.

If multi-vault per-vault embedding becomes real, a follow-up ADR can promote this to a
`provider_config`-style scoped setting; the global flag is forward-compatible (it becomes the
global-scope default). **The global-vs-per-vault choice is the one owner decision (§6).**

### 2.2 Ingest behavior when off — skip vectorization, keep everything else (I1)

When `EMBEDDINGS_ENABLED=false`, `upsert_vector()` **returns early without embedding or
upserting** to Qdrant. Postgres metadata persistence (`persist_metadata`), K5 wikilink parse,
K3 index update, K4 log append, and `dataVersion` bump **all still run**. Pages are fully
indexed in Postgres; only the Qdrant vector is absent. This keeps ingest a single incremental
pass (I1) — no page is left half-written and no re-scan is implied.

### 2.3 Retrieval/degrade contract — lexical phase replaces Phase 1 (precise)

`retrieve()` gains a branch on `settings.embeddings_enabled`:

- **Enabled (default):** unchanged — Phase 1 = dense Qdrant search (ADR-0022).
- **Disabled:** Phase 1 = **`_phase1_lexical_search`** — a Postgres-only keyword match over
  **live** pages (`deleted_at IS NULL`, `vault_id` scoped), ranked deterministically:
  1. Tokenize the query (lowercase, split on non-alphanumeric — reuse the existing `_SLUG_RE`
     style in `rag/retrieval.py`; no tokenizer dependency, char/4 budget unchanged).
  2. Match against `pages.title` first (case-insensitive `ILIKE`/contains on each token),
     then against the **source-file body** the assembler already loads (Phase 4 reads bodies
     anyway — reuse that read; do not add a second file walk, I1).
  3. Score = simple term-overlap count (title hits weighted above body hits). Deterministic,
     bounded by `k` (same `k` parameter, default 8). No `to_tsvector` requirement — plain
     `ILIKE` over `title` plus a bounded body scan keeps it dependency-free and Obsidian-safe;
     a Postgres FTS index MAY be added later as an optimisation (non-blocking, follow-up).
  4. The resulting candidates feed **Phases 2–4 unchanged** (graph-expansion over `edges`,
     token budget, assembly + `[n]` citations). Graph expansion (Phase 2) still works because
     it operates on `edges`/`links`, not vectors — so even lexical seeds get graph context.

**Contract guarantees preserved:** still a single bounded pass (I7); still zero inference calls;
still zero extra `vault/` walk beyond the bodies Phase 4 already loads (I1); `citations` count
still equals distinct `[n]`; `data_version` unchanged across the call (AC-F5-5). `/search` and
`/chat` return results instead of erroring — they just have no dense semantic ranking.

**Body-scan bound (I7):** the lexical body scan is capped at the same live-page set Phase 1
would have seeded (`k`-bounded after a Postgres-side title prefilter) — it is NOT an unbounded
scan of every page's body. The implementer prefilters by title `ILIKE` to a bounded candidate
set, then scores bodies only for that set. Reject any implementation that loads every page body.

### 2.4 Toggling an already-embedded vault — no re-scan either direction (I1)

- **on → off:** existing Qdrant vectors are **left in place** (not deleted). Ingest stops
  adding new ones; retrieval ignores Qdrant. Cost: stale vectors linger harmlessly.
- **off → on:** newly ingested/changed pages get vectors via the normal incremental path.
  Pages ingested **while off have no vector** and will be invisible to dense Phase 1 until they
  next change (which re-triggers `upsert_vector`). This is the I1-correct behavior: **we do NOT
  bulk re-embed the whole vault on toggle** (that would be the exact re-scan I1 forbids).
- **Backfill is an explicit, separate, bounded operation — out of scope here.** If the owner
  wants to vectorize the gap after turning embeddings back on, that is a future bounded
  "reindex-embeddings" op (its own ADR, with `max_iter`/budget per I7), NOT an automatic
  side-effect of the toggle. Flagged, not built.

### 2.5 Startup guard — do not fail-fast when embeddings are off

`_validate_embedding_and_collection()` (main.py:2944) is **skipped entirely** when
`EMBEDDINGS_ENABLED=false`: no `probe_dimension()` call, no `ensure_collection()`. Startup
succeeds with the embedding service absent. When enabled, behavior is unchanged (ADR-0004
fail-fast still applies). Implementer logs one INFO line stating the mode.

### 2.6 MCP `search_wiki` and Settings UI

- `search_wiki` (MCP, `server.py:65`) currently does a raw Qdrant lookup. When embeddings are
  off it should **degrade the same way** — simplest correct path: route `search_wiki` through
  `retrieve()` (it already returns ranked refs), OR add the same `embeddings_enabled` branch.
  ai-agent-engineer picks; the contract is "no error when off, returns lexical hits."
- Settings → Embeddings tab (read-only today) shows the **enabled/disabled** state and, when
  disabled, a note that search is lexical-only. Sourced from a new read-only field on
  `GET /config/embedding` (`embeddings_enabled: bool`). No secret exposure.

---

## 3. New config / env / schema

| Kind | Name | Type / default | Read in | Notes |
|------|------|----------------|---------|-------|
| env | `EMBEDDINGS_ENABLED` | bool, default `true` | `config.py` | Global. Off ⇒ skip vectorize + skip startup probe + lexical retrieval. |
| API field | `embeddings_enabled` | bool | `GET /config/embedding` | Read-only; for Settings UI. |

**No DB schema change. No migration. No D2 (ER) change.** The flag is global config, not a
column. (Explicitly answering the design question: **no column is added.** A column would only
be needed for per-vault scope, which §2.1 defers.)

---

## 4. Acceptance check (DoD)

1. `EMBEDDINGS_ENABLED=false`: backend starts with **no** embedding service reachable (no
   `probe_dimension`/`ensure_collection` call).
2. Ingest with flag off: page appears in `pages` (Postgres), with `[[wikilinks]]`, log entry,
   and `dataVersion` bump — and **no** Qdrant point is created.
3. `/search?q=...` with flag off returns lexical results (title/body keyword hits) with
   contiguous `[n]` citations, `data_version` unchanged — does **not** 500.
4. Phase 2 graph-expansion still runs in lexical mode (lexical seed → edge-expanded candidates).
5. Toggle off then on: no bulk re-embed occurs; only changed pages get vectors (I1 proven).
6. `GET /config/embedding` returns `embeddings_enabled`; Settings tab reflects it.
7. The lexical body scan is `k`-bounded (no all-pages body load) — proven by a test with N≫k
   pages.

---

## 5. Consequences

**Positive** — Synapse runs with zero embedding infra (offline / cost-zero / privacy); search
degrades gracefully instead of 500ing; ingest stays incremental; toggling is cheap and
re-scan-free.

**Trade-offs (explicit)** — lexical-only search loses semantic recall (exact/substring matches
only); off→on leaves a vector gap for pages ingested while off (intentional per I1; backfill is
a deferred bounded op). `ILIKE`-based lexical match is crude vs Postgres FTS — acceptable for a
degrade mode; FTS is a non-blocking follow-up. Global scope means all vaults share the flag
until a future per-vault ADR.

**Invariant check** — I1: toggle never triggers a re-scan; ingest stays a single incremental
pass. I7: lexical phase is `k`-bounded, single pass, no provider loop. I9: reuses Postgres +
existing file reads; adds no search engine. I5: ingest still validates frontmatter and writes
via the shared path; vault stays Obsidian-valid. I2/I3/I4/I6/I8: untouched. **No invariant is
traded for convenience.**

## 6. Decision the owner must make before coding

**Flag scope.** RECOMMENDED: **global env `EMBEDDINGS_ENABLED`** (matches today's global
embedding config; smallest correct change; forward-compatible with a later per-vault promotion).
Alternative: **per-vault** via a `provider_config`-style scoped row + resolver + UI — defer
unless multi-vault embedding control is needed now (materially more work). Build proceeds global
unless the owner chooses per-vault.
