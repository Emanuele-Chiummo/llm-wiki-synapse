# ADR-0076 — Block-based ingest pipeline and orchestrator decomposition (v1.7.0)

- **Status:** Accepted
- **Date:** 2026-07-14
- **Supersedes:** the JSON `Analysis` / `pages[]` generation contract introduced for the orchestrated
  path (ADR-0011 shapes retained as DB/validation types, no longer the provider wire format for ingest)
- **Amends:** ADR-0073 (generation lifecycle parity) — replaces the 6-type JSON `GENERATION_SCAFFOLD`
  with the ported llm_wiki markdown/blocks prompt; ADR-0009 (bounded loop) — loop body reworked, bounds unchanged
- **Invariants touched:** I1, I5, I6, I7, I8, I9
- **Reference:** `docs/reference/LLMWIKI-CORE-LOGIC-v0.6.3.md` (authoritative behavior spec)

## Context

Synapse's orchestrated ingest (Local/API providers) asks the model for JSON — an `Analysis`
object, then a `pages[]` array — parsed inside the provider layer. The v1.6.0 "generation parity"
work grew the JSON generation scaffold from 3 page types to 6 with explicit evidence gates, roughly
doubling its length and burying the single wikilink instruction in a trailing "Naming" clause
(`ingest/provider/_common.py:133`).

Two problems resulted:

1. **Link regression (1.6.0 vs 1.5.6).** A git-diff audit (`efccf7c..c9f2cc3`) confirmed that no
   wikilink-producing code was touched: the enrich post-pass, the "LINK TO THESE" catalogue (caps
   400/8000), link parsing and `related:` derivation are all byte-identical across the two releases.
   The regression is therefore driven by the generation **prompt**: shorter, thinner page bodies
   spread across six page types, with the linking instruction de-emphasized, produce fewer inline
   `[[wikilinks]]` and less mention surface for the (unchanged) enrich pass. A second contributor is
   the v1.6.0 query-page validator (`loop.py`), which invalidates a whole batch when a `query` page
   lacks a `## Research queries` block with ≥2 queries — a rule the reference implementation does not
   have, causing retries and regenerated (often shorter) output.

2. **Divergence from the reference.** nashsu/llm_wiki v0.6.3 uses **no JSON mode anywhere**. Its
   ingest is two SSE text stages — a free-markdown analysis and a generation step that emits
   `---FILE:` / `---REVIEW:` blocks — parsed by a tolerant, precisely specified state machine. The
   prompts *are* the behavior; porting their shape is the most direct route to output parity and, as
   a side effect, works on weak local models that cannot reliably honor JSON schemas.

Separately, `ingest/orchestrator.py` had grown to ~3,850 lines mixing context assembly, prompts,
parsing, sanitizing, page writing, aggregates and pipeline coordination — the natural seams for the
port coincide with the reference's module boundaries.

## Decision

### 1. Adopt the reference's two-stage text pipeline for the orchestrated path

- **Analysis** stage produces free markdown with the reference's sections (Key Entities, Key
  Concepts, Main Arguments & Findings with subject-boundary rules, **Connections to Existing Wiki**,
  Contradictions & Tensions, Recommendations). Inputs: `purpose.md` + `schema.md` + the existing-pages
  catalogue + source. Not JSON.
- **Generation** stage emits `---FILE: <path>---` … `---END FILE---` blocks and optional
  `---REVIEW: <type> | <title>---` blocks. The schema.md "Page Types" table is embedded as the
  authoritative routing rule. Wikilink guidance is restored to prominence (body cross-reference
  instruction + "if the analysis found connections, add cross-references"), matching the reference.
- A **conditional dedicated review stage** runs only when generation output is ≥10,000 chars or
  ≥4 FILE blocks, emitting 1–5 high-signal REVIEW blocks with 2–3 `SEARCH:` queries.

Provider options mirror the reference: `temperature 0.1`, reasoning off, generation `max_tokens`
tiered 8192/16384/24576/32768 by context size; context budgets measured in **characters**
(default 204,800; source budget `clamp(ctx×0.6, 8k, 300k)`).

### 2. Parsing and sanitizing move out of the provider layer (I6 preserved)

`InferenceProvider` gains one transport method: `complete(system, prompt, *, max_tokens) -> str`
(raw text). FILE/REVIEW/LINT block parsing (`ingest/blocks.py`) and the 4-rule sanitizer
(`ingest/sanitize.py`) are provider-neutral pure functions. Providers become transport only; no
provider ever branches on page shape. The CLI delegated path keeps writing through MCP `write_page`,
but its system prompt is assembled from the **same** `ingest/prompts.py` sections as the orchestrated
generation prompt — a CI contract test asserts the two share the normative sections so they cannot
drift.

### 3. Bounded loop retained; parity relaxations applied (I7)

The loop keeps its `max_iter` + `token_budget` bounds and cancel event. The body becomes:
analysis `complete()` → generation `complete()` → parse + sanitize + writer-level validation
(source-summary present, origin in `sources[]`, schema routing, frontmatter parses) → on failure,
augment the prompt with explicit parse/validation errors and retry. **Empty output is a failure**
(reference rule). Two v1.6.0 gates are relaxed for parity:

- Frontmatter `lang` becomes **optional** (the reference has no such field); `pages.lang` is filled
  by detection so search/i18n keep working.
- The query-page `## Research queries` validator is **downgraded from batch-invalidating to
  advisory** (logged + recorded as a metric, never a retry trigger).

There is **no** code path that discards a generation solely for not starting with `---FILE:` — the
reference has none; that text is a prompt-only contract and the parser tolerates preamble.

### 4. Wikilink density comes from prompts; enrich becomes opt-in (default OFF)

Because the reference's `enrich-wikilinks.ts` is dead code (unused by production) and link density is
a pure function of the prompts, Synapse's `ops/enrich_wikilinks.py` post-pass flips to
`wikilink_enrich_enabled = False` by default. It remains one config/Settings toggle away. Keeping it
ON would make the link-density parity band unfalsifiable (one could not attribute a restored count to
the prompt fix). `related:` frontmatter is taken from the model's bare-slug output (repaired by the
sanitizer); the prior derive-from-resolved-links path (cap 8) is demoted to a fallback when the model
omits it.

### 5. Orchestrator decomposition (façade-preserving)

`ingest/orchestrator.py` is split into `context.py`, `prompts.py`, `blocks.py`, `sanitize.py`,
`writer.py`, `aggregates.py` (ADR-0078) and `pipeline.py`, with `wiki/schema.py` for routing
(ADR-0077). `orchestrator.py` remains a thin façade re-exporting every public seam so the ~40
importers (`mcp/server.py`, `watcher.py`, routers, tests) and their monkeypatch targets are
unchanged. Extraction (no behavior change) and behavior changes land in separate PRs.

### 6. Rollback lever

`ingest_pipeline_format: "blocks" | "json"` (default `"blocks"`) selects the new path or the 1.6.x
JSON path. It exists to de-risk the release against weak-model regressions and is scheduled for
removal in 1.8.

## Consequences

**Positive**
- Direct route to output parity with the reference; the link regression is fixed at its source (the
  prompt), verified by the parity harness (ADR-0083) with a "total wikilinks ≥ 1.5.6 baseline" sentinel.
- Text blocks work on providers without JSON mode (weak Ollama models), strengthening I6.
- A single `prompts.py` source of truth for orchestrated and delegated paths removes prompt drift.
- The 3,850-line orchestrator becomes navigable, testable modules mirroring the reference.

**Negative / risks**
- Weak local models may still fail to emit well-formed FILE blocks; mitigated by the sanitizer,
  bounded retry with explicit errors, a guaranteed deterministic fallback source summary, and the
  `json` rollback flag.
- Existing vaults: no page rewrite; the new pipeline only changes how *new* ingests are prompted.
- Behavior parity is soft (LLM nondeterminism at temp 0.1) — asserted via tolerance bands, not bytes.

**Invariant notes**
- I1 (incremental), I5 (Obsidian frontmatter), I6 (provider-pluggable), I7 (bounded loop) preserved.
- I8: this ADR, refreshed ingest sequence diagrams (D3), and the parity harness satisfy the docs gate.
- I9: reuses the reference's proven design rather than inventing a new one.
