# ADR-0063 — Ingest-quality parity: long-source chunking, re-ingest body-merge, wrong-language drop

- **Status:** Accepted
- **Date:** 2026-07-09
- **Sprint:** v1.3.13 — nashsu/llm_wiki parity (I1 batch)
- **Extends:** ADR-0007 (InferenceProvider ABC + orchestrated loop) · ADR-0009 (bounded loop / I7)
  · ADR-0010 (shared `write_wiki_page` seam) · ADR-0011 (locked ingest DTOs). This ADR does **not**
  supersede any of them: the two-step CoT (`analyze → generate → validate → retry`), the
  `max_iter`+`token_budget` bounds, the single write path, and the frozen DTOs stand unchanged.
  It **adds** three orchestrated-route ingest-quality behaviors, each behind a config knob.
- **Features:** F3 (two-step CoT ingest, source traceability, language-aware) · F17/I6 (every LLM
  sub-step routes through `InferenceProvider`) · K6 (frontmatter provenance preserved on merge).
- **Reference:** R1 (nashsu/llm_wiki — `src/lib/ingest.ts::analyzeLongSourceInChunks`,
  `src/lib/page-merge.ts::mergePageContent`, `ingest.ts::contentMatchesTargetLanguage` +
  `detect-language.ts`).
- **Invariants owned:** **I6** (no provider/model/base_url/key hardcoded; no `isinstance` /
  `provider_type` branch — all three features call the existing `analyze` / `chat` seams) · **I7**
  (chunk count capped by `ingest_long_source_max_chunks`; merge is a single timed call; the
  language guard makes **zero** provider calls) · **I1** (the merge still writes through
  `write_wiki_page`, one `data_version` bump, upsert by `(vault_id, file_path)`, hash of the full
  file bytes incl. trailing `\n`).

---

## 1. Context

Three ingest-quality behaviors exist in the reference (`nashsu/llm_wiki`) that Synapse lacked.
Each is a real correctness gap, not a nicety:

1. **Long sources are silently truncated.** Synapse sent the whole source to `analyze()` and
   relied on the model's context window. Past the window the document tail is dropped with no
   signal — the analysis (and therefore every generated page) simply misses the back half.
2. **Re-ingest clobbers prior contributions.** When a second source enriches an existing
   entity/concept page, `write_wiki_page` overwrote the body with the newly generated text.
   The first source's unique content was lost (frontmatter `sources[]` were unioned, so the page
   still *claimed* both sources while only reflecting one — worse than an honest overwrite).
3. **Off-language pages leak in.** Language was enforced by prompt only. A model that ignored the
   directive (common for short idiomatic sources, or a multilingual source) produced a page in
   the wrong language that was written and indexed anyway.

## 2. Decision

Port all three, **Synapse-idiomatic** (provider-abstracted, bounded, degrade-safe). They apply to
the **orchestrated route only** (Local / API); the delegated/CLI route runs the agent's own loop
and is a documented gap (§7, mirroring ADR-0037 §7). Every knob is env-overridable (`app/config.py`)
and each has a safe default.

### 2.1 Feature 1 — long-source chunked analysis + checkpointing

`app/ingest/long_source.py::analyze_source()` is a drop-in for `provider.analyze()`, called by the
orchestrated loop (`loop.py`). When `len(source_text) > ingest_long_source_char_threshold` it:

- splits the source into paragraph-boundary chunks of ~`ingest_long_source_chunk_chars` with a
  small overlap (`split_into_chunks`);
- **caps** the chunk count at `ingest_long_source_max_chunks` (I7 — never one `analyze()` per
  paragraph of a huge document);
- calls `provider.analyze()` **once per chunk** (I6 — the same seam, N times) and **merges** the
  per-chunk `Analysis` objects (`merge_analyses`: union topics / entities / suggested_pages with
  order-preserving dedup, modal language, concatenated summaries);
- persists a **best-effort on-disk checkpoint** under
  `vault_root/.synapse/ingest-progress/source-<hash>.json` after each successful chunk, so a
  mid-way failure or retry **resumes** from the last completed chunk.

**Degrade-safe:** a per-chunk failure keeps every prior chunk's result and merges what succeeded;
if **every** chunk fails, it falls back to a single whole-source `analyze()` call — i.e. exactly
the pre-parity behavior, so a genuine provider outage surfaces through the loop's normal fallback
path (ADR-0009 §4), not here. At/under the threshold (or with the threshold set to `0`) the common
case is a single `analyze()` call, unchanged.

### 2.2 Feature 2 — LLM body-merge on re-ingest

`app/ingest/page_merge.py::maybe_merge_page_body()` is invoked inside the shared
`write_wiki_page` seam **only when the orchestrated write site passes the run's `provider`** (a new
keyword-only `provider=None` arg; every other caller — the MCP/CLI write path, the REST create
endpoints, lint/review — passes `None` and is therefore never merged). When the target
`(vault_id, file_path)` already exists with a **meaningful** prior body, it asks the provider to
merge old + new **bodies** via the `chat()` seam (I6) and uses the merged body.

Synapse merges **only the markdown body**, never the frontmatter block: `write_wiki_page` already
owns the reference's "locked fields" (type/title preserved, `created` carried forward, `updated`
stamped today) and "array-field union" (`sources[]`). This keeps the merge surface minimal.

**Bounded (I7):** a single `chat()` call wrapped by `ingest_reingest_merge_timeout_seconds`; cost
folds into the run-scoped `UsageAccumulator` the provider is already bound to.
**Degrade-safe:** disabled config, no meaningful prior body, provider failure, timeout, or a
sanity-check rejection (empty / body shorter than 70 % of the longer input — the reference's
truncation guard) all return the **new** body — the pre-parity overwrite. One `data_version` bump,
one write (I1).

### 2.3 Feature 3 — wrong-language page drop

`app/ingest/language.py` provides deterministic, dependency-free **script-family** detection (no
provider call). `orchestrator._drop_wrong_language_pages()` runs after `generate()` (post-loop,
before the source-summary guarantee and the write loop): each generated page whose dominant body
script-family contradicts the resolved target output language (`Analysis.language`) is **dropped**
(logged). Only **cross-script** mismatches drop (Chinese body vs English target); intra-Latin
differences never do (English mis-detected as Italian for a short sample is not worth a drop — the
reference makes the same call). **Exempt:** `index`/`overview`/`log` (never in the generated batch)
plus `source` and `entity` pages (F3 traceability + entities legitimately quote cross-language
proper nouns). If the guard empties the batch, `_ensure_source_summary` still guarantees the F3
source-summary page. Degrade-safe: any detection error keeps the page.

## 3. Config knobs (all env-overridable; `app/config.py`)

| Knob | Default | Env var |
|------|---------|---------|
| `ingest_long_source_char_threshold` | `48000` | `INGEST_LONG_SOURCE_CHAR_THRESHOLD` |
| `ingest_long_source_chunk_chars` | `24000` | `INGEST_LONG_SOURCE_CHUNK_CHARS` |
| `ingest_long_source_max_chunks` | `8` | `INGEST_LONG_SOURCE_MAX_CHUNKS` |
| `ingest_long_source_checkpoint_enabled` | `true` | `INGEST_LONG_SOURCE_CHECKPOINT_ENABLED` |
| `ingest_reingest_merge_enabled` | `true` | `INGEST_REINGEST_MERGE_ENABLED` |
| `ingest_reingest_merge_timeout_seconds` | `60.0` | `INGEST_REINGEST_MERGE_TIMEOUT_SECONDS` |
| `ingest_language_guard_enabled` | `true` | `INGEST_LANGUAGE_GUARD_ENABLED` |

Setting `ingest_long_source_char_threshold=0` disables chunking; the two `*_enabled` flags disable
their features (pre-parity behavior). The language guard also respects UI overrides
(`config_overrides.effective_bool`).

## 4. How each routes through the provider abstraction (I6)

- **Chunked analysis:** `analyze_source` calls `provider.analyze()` — the *same* seam the
  single-source path uses — once per chunk. No new provider method, no class/type branch.
- **Body-merge:** `maybe_merge_page_body` consumes `provider.chat()`. No model id / base_url /
  key appears outside `provider/`.
- **Language guard:** pure Unicode-range analysis — **no** provider call at all.

No `isinstance` / `provider_type` / class-name branch is introduced anywhere.

## 5. Alternatives considered

- **Rely on ever-larger context windows** for long sources — rejected: silent truncation is
  window-dependent and invisible; chunk-and-merge is deterministic and backend-neutral.
- **Merge full files (frontmatter + body) like the reference** — rejected: `write_wiki_page`
  already owns frontmatter; merging it twice risks type/title drift (breaks wikilinks). Body-only
  is the smaller, safer surface.
- **Language identification per-word / via a library** — rejected (I9 "do not reinvent" + no new
  dep): script-family detection catches the real cross-script defect deterministically without a
  provider call or a package.

## 6. Consequences

- Long documents are analyzed in full; re-ingest preserves every source's contribution; off-language
  pages are dropped before they pollute the graph.
- Cost is bounded and logged: chunk count is capped, the merge is one timed call folded into the
  run ledger, the guard is free.
- The common case (normal-size source, first ingest, on-language) is byte-for-byte unchanged.

## 7. Recorded gap — CLI/delegated route

All three features act on the **orchestrated** (Local / API) route. The **delegated/CLI** route
(`CliAgentProvider`, `supports_agentic_loop=True`) runs the agent's own loop and writes pages via
MCP `write_page` → `write_wiki_page(provider=None)`; it therefore does **not** chunk long sources,
does **not** LLM-merge on re-ingest, and is **not** language-guarded here. This matches ADR-0037 §7
and ADR-0036's treatment of the enrichment pass: the CLI agent is trusted to manage its own
analysis/merge/language within its loop. Closing this gap (e.g. an MCP-side merge hook) is a
reserved follow-up.

## 8. Do-NOT list

1. Do **not** run the chunk loop as `while True` — it is `for idx in range(len(analyses), chunk_total)`
   with `chunk_total ≤ ingest_long_source_max_chunks` (I7).
2. Do **not** let a chunk failure fail the whole ingest — keep prior chunks; total failure falls
   back to a single `analyze()`.
3. Do **not** merge the frontmatter block — merge only the body; `write_wiki_page` owns
   type/title/created/updated/sources (I1/I5).
4. Do **not** accept a merged body that fails the shrink sanity check — keep the new body.
5. Do **not** make the language guard call a provider — it is deterministic script detection.
6. Do **not** drop `source`/`entity`/meta pages on the language guard (F3 + proper-noun safety).
7. Do **not** hardcode a backend or branch on `isinstance` / `provider_type` (I6) — use the
   `analyze` / `chat` seams.
8. Do **not** let checkpoint I/O block or fail ingest — all checkpoint reads/writes are swallowed.
