# ADR-0036 — Wikilink-enrichment post-pass (substitution-apply, bounded, provider-agnostic)

- **Status:** Accepted
- **Date:** 2026-06-30
- **Sprint:** v0.5 (M5 hardening — graph connectivity / "direct link ×3" signal)
- **Features:** F4 (knowledge graph — restores the *direct link ×3* signal so clusters connect) ·
  F3 (ingest generation quality) · K5 (`[[wikilink]]` syntax + parser)
- **Reference:** R1 (nashsu/llm_wiki) — `enrich-wikilinks.ts`: the LLM returns a list of **substitutions**
  (mention → existing page title), and **code applies them**; the model is never asked to rewrite whole
  documents. This ADR adopts that exact pattern.
- **Invariants owned:** **I6** (HEADLINE — the enrichment LLM call routes through `InferenceProvider`
  via `resolve_provider_config`; provider-agnostic, no hardcoded backend/model) · **I7** (HEADLINE —
  bounded: `max_iter` + `token_budget`, capped substitution count, timeout, `total_cost_usd` logged) ·
  **I5** (only inserts `[[wikilinks]]` into page **bodies**; never touches frontmatter) · **I1**
  (re-indexes only the edited pages incrementally; no rescan; the `links` rows are re-derived per page)
- **Author:** solution-architect

---

## 1. Context

### 1.1 The problem: disconnected clusters, dead "direct link ×3" signal

The F4 graph weights page pairs with four signals (ADR-0012/0016), the strongest of which is the
**direct `[[wikilink]]` ×3** term — a structural edge whenever page A's body links page B. But the
orchestrated generation step writes each page largely in isolation: the LLM produces a page about
*"Transformer architecture"* without knowing that a page *"Attention mechanism"* already exists in the
vault, so it writes the bare phrase "attention mechanism" as plain text instead of `[[Attention
mechanism]]`. The consequence is a graph of **disconnected single-node clusters** — the source-overlap
×4 term connects pages that share a `raw/source`, but conceptually-related pages from *different* sources
never link, so the graph never forms the connected concept clusters Karpathy's wiki is supposed to grow.

This is exactly the gap nashsu/llm_wiki's `enrich-wikilinks.ts` post-pass fixes: after generation, a
**dedicated enrichment stage** scans the freshly-written pages for **mentions of existing page
titles/entities** and turns the first such mention into a `[[wikilink]]`, re-introducing the direct-link
edges that make clusters connect.

### 1.2 The robust pattern: LLM returns substitutions, code applies them

The key design choice from R1 (and the reason this is robust): **the LLM does not rewrite the document.**
Asking a model to "return the whole page with wikilinks added" is fragile — it silently reflows prose,
drops content, corrupts frontmatter, and burns tokens proportional to document size. Instead:

- The LLM is given the page text + a **bounded list of existing page titles** and asked to return a
  compact **substitution list**: `[{ "mention": "attention mechanism", "target": "Attention mechanism" }, …]`
  — i.e. "this exact substring in the body refers to that existing page".
- **Deterministic code applies** each substitution: find the **first occurrence** of `mention` in the
  **body** (not frontmatter, not inside an existing `[[…]]`), wrap it as `[[target]]` (or `[[target|mention]]`
  when the surface form differs from the title), and stop (single-mention — one link per target per page,
  matching R1; avoids link spam). Output size is bounded by the *number of substitutions*, not the
  document length; the body is never regenerated.

This makes the pass **robust** (no whole-doc rewrite → no prose drift, no frontmatter corruption, no
content loss) and **cheap** (the model emits a short JSON list).

### 1.3 Ground truth consumed (reuse, do not reinvent — I9)

- **`resolve_provider_config("ingest", vault_id)` + `resolve_provider`** (`provider_config_service.py`,
  `ingest/provider`) — the backend-neutral resolution path (I6). The enrichment call routes through it,
  operation `"ingest"`, exactly like `propose_reviews` (ADR-0034 §4.3) and Deep Research (ADR-0024).
- **`InferenceProvider.chat(messages, retrieval_context)`** (`ingest/provider/base.py`) — the existing
  backend-neutral text/JSON-in-JSON-out method. Enrichment uses **`chat()`** with a structured-output
  prompt; **no new ABC method** is added (same discipline as ADR-0024/0034 — text in, substitution JSON
  out, provider-agnostic).
- **`run_ingest_pipeline`** (`ingest/orchestrator.py`) — the orchestrated branch that writes pages via
  `write_wiki_page`, then runs the F9 `propose_reviews` hook (ADR-0034). The enrichment pass is a new
  hook in the **same post-write seam**, running **before** `propose_reviews` (so proposals see the
  enriched link graph) and **after** all pages are written (so every just-written title is linkable).
- **`parse_wikilinks` / `persist_links`** (`wiki/links.py`) + **`_WIKILINK_RE = [[…]]`** — the K5 grammar.
  Enrichment re-runs these on each modified body so the new links land in the `links` table (feeding the
  F4 ×3 signal).
- **The incremental wiki-write/re-index primitives** (`persist_metadata` content_hash refresh,
  `upsert_vector`, `update_index`, `bump_version`) — enrichment re-indexes only the pages it edited
  (I1), the same way ADR-0035's PUT re-indexes a single edited page.
- **`UsageAccumulator` + the `$1` anomaly WARNING** (the ingest cost path) — `total_cost_usd` is logged
  on the enrichment run row (I7), same finalize path every provider call uses.
- **`REVIEW_PROPOSE_*` bounds precedent** (ADR-0034 §4.3) — the `WIKILINK_ENRICH_*` env knobs mirror that
  established pattern.

---

## 2. Decision summary

Add a bounded, provider-agnostic **wikilink-enrichment post-pass** (`ops/enrich_wikilinks.py`,
`enrich_wikilinks(written_pages, vault_id)`) that runs **once per orchestrated ingest run**, after
generation has written all pages and before `propose_reviews`. It implements the R1 **substitution-apply**
pattern: the LLM returns substitutions; code applies them single-mention; only `[[wikilinks]]` are added
to page **bodies**; modified pages are re-indexed incrementally.

```python
# ops/enrich_wikilinks.py  (signatures only — no implementation in this ADR)

@dataclass(frozen=True)
class WikilinkSubstitution:
    page_id: uuid.UUID        # the just-written page whose body is enriched
    mention: str              # exact body substring the LLM flagged
    target_title: str         # existing page title it refers to (must exist in the candidate set)

async def enrich_wikilinks(written_pages: list[Page], vault_id: str) -> EnrichResult:
    """Once-per-run post-pass. Provider-agnostic (resolve_provider_config('ingest')), bounded
    (WIKILINK_ENRICH_* caps + token_budget + timeout, cost logged). Applies substitutions
    single-mention to BODIES ONLY; re-indexes only the edited pages (I1). Fire-and-forget:
    failure logs a WARNING and never fails the ingest (pages are already written)."""
```

### 2.1 Flow

```
1. Build the candidate target set: existing page titles in the vault (a BOUNDED title list —
   capped at WIKILINK_ENRICH_MAX_CANDIDATES, e.g. 500 — NOT page contents), plus the titles of the
   pages just written this run. Bounded indexed read of `pages.title` (live rows). No vault walk (I1).
2. Anti-spam / cost gate: skip entirely if there are no candidate targets, or the written content is
   trivial (< WIKILINK_ENRICH_MIN_CHARS). Skipping = zero cost, zero LLM call.
3. Resolve the ingest provider (resolve_provider_config("ingest", vault_id) → resolve_provider) — I6.
   No provider configured → skip with a WARNING (NEVER hardcode a backend — I6 hard rule).
4. ONE bounded InferenceProvider.chat() call (operation "ingest"): given each written page's body +
   the candidate title list, return a structured substitution list {page_id, mention, target_title}.
   Bounds (I7): asyncio.wait_for(WIKILINK_ENRICH_TIMEOUT_SECONDS); token_budget from the resolved row;
   substitution count truncated at WIKILINK_ENRICH_MAX_SUBS (e.g. 100). One call, no loop, no retry.
5. VALIDATE every substitution in code (anti-hallucination): drop any whose target_title is NOT in the
   candidate set, whose mention is not found in that page's body, or whose target == the page's own
   title (no self-links). Only validated substitutions are applied.
6. APPLY deterministically, per page, BODY ONLY: for each validated substitution, find the FIRST
   occurrence of `mention` in the body that is not already inside an existing [[…]] span, and wrap it
   as [[target_title]] (or [[target_title|mention]] when the surface form differs). Single-mention:
   one link per (page, target) — stop after the first. Frontmatter is never touched (I5).
7. For each page actually modified: write the new bytes atomically (temp + os.replace), recompute
   content_hash, then re-index INLINE/incrementally — persist_metadata (hash) → upsert_vector (body) →
   parse_wikilinks + persist_links (the new [[links]] land in `links` → F4 ×3 signal) → bump_version().
   Only the modified pages are touched (I1). data_version is bumped ONCE for the whole pass
   (batched at the end), not per page.
8. Log total_cost_usd (+ $1 anomaly WARNING) on the run; return EnrichResult{pages_enriched,
   links_added, total_cost_usd}.
```

### 2.2 Why once-per-run, not per-page

Like ADR-0034's `propose_reviews`: a single call lets the model see the whole batch + the full candidate
title set, collapses N calls to ≤1 (lower cost/latency, I7), and matches R1's batch enrichment. The
substitution-apply design keeps the output bounded regardless of batch size.

---

## 3. Robustness: substitutions, not rewrites (the R1 discipline)

The entire safety case rests on **the model never emitting page content**:

- The model returns **only** `{mention, target_title}` pairs. The most it can do wrong is propose a bad
  substitution — which step-5 validation **drops** (unknown target, mention-not-in-body, self-link).
- The body is mutated by **deterministic string surgery** on a validated, body-found mention, anchored
  outside existing `[[…]]` spans (reusing the K5 `_WIKILINK_RE` grammar to avoid double-wrapping). The
  prose, the frontmatter, and every other byte are untouched.
- **Single-mention** (first occurrence only) prevents link spam and keeps the edit minimal and
  reviewable — exactly R1's behavior.

Contrast with the rejected "ask the model to return the rewritten page": that would risk silent content
loss, prose drift, frontmatter reordering (the DEFECT-F13-002 corruption class, ADR-0026 §4.3), and
token cost proportional to document size. **Substitution-apply is the only accepted design.**

---

## 4. Bounds and failure (I7)

- **≤1 provider call** for the whole pass (no loop, no retry), wrapped in
  `asyncio.wait_for(WIKILINK_ENRICH_TIMEOUT_SECONDS)`.
- **`token_budget`** from the resolved provider row caps generation; the candidate title list is itself
  capped (`WIKILINK_ENRICH_MAX_CANDIDATES`) so the prompt is bounded.
- **`WIKILINK_ENRICH_MAX_SUBS`** truncates the applied substitution list — never an unbounded edit set.
- **Anti-spam gate** (§2.1 step 2) skips the call (zero cost) on trivial runs or empty candidate sets.
- **`total_cost_usd`** logged with the `$1` anomaly WARNING, same as every provider call.
- **Fire-and-forget failure degradation:** a timeout / no-provider / parse error / validation-empties-the-
  list → the pass applies **zero** substitutions, logs a WARNING, and **never fails the ingest** (the
  pages are already written and valid; enrichment is an additive improvement, not a correctness gate).
  This mirrors `propose_reviews` (ADR-0034 §4.3) and the deep-research safety posture.

`WIKILINK_ENRICH_ENABLED` (default `true`) lets an operator disable the pass entirely for zero-cost
ingest.

---

## 5. Obsidian compatibility (I5) and graph effect (F4)

- **I5:** enrichment edits **bodies only**. It inserts `[[target_title]]` / `[[target_title|mention]]`
  using the K5 grammar; it never parses or rewrites the frontmatter block (no PyYAML round-trip on the
  write path — the frontmatter bytes are preserved verbatim, same discipline as ADR-0035 §5). The result
  is a valid Obsidian note with real working wikilinks; `test_obsidian_check.py` stays green.
- **F4:** every applied substitution becomes a `links` row (via `persist_links`), which the F4 edge
  engine reads as a **direct link** → the **×3** weight term (ADR-0012/0016). These new structural edges
  are exactly what connects previously-disconnected clusters. The new edges flow into the graph on the
  next debounced FA2 recompute (triggered by the single `bump_version()` — I2 untouched; no inline FA2).

---

## 6. Flagged tensions & risks

1. **Over-linking / wrong target.** Mitigated by single-mention (first occurrence only) + step-5
   validation (target must be a real page; mention must exist in the body; no self-links) + the
   `WIKILINK_ENRICH_MAX_SUBS` cap. A wrong link is recoverable: the human can remove it in the
   CodeMirror editor (ADR-0035) and re-save.
2. **Candidate-set truncation.** When the vault exceeds `WIKILINK_ENRICH_MAX_CANDIDATES` titles, the
   candidate list is truncated (most-recent / most-linked first). Some valid links may be missed in very
   large vaults — acceptable: enrichment is best-effort additive, not exhaustive, and the bound is an I7
   requirement. Tunable.
3. **Delegated (CLI) ingest is not enriched here.** The CLI provider runs its own agent loop and links
   pages itself (F17 delegated path, `supports_agentic_loop=True`); the orchestrator does not enumerate
   the pages it wrote via MCP `write_page`. So `enrich_wikilinks` attaches to the **orchestrated** branch
   only (same boundary as `propose_reviews`, ADR-0034 §9 risk 1). **Not a violation** — an explicit,
   recorded gap; the CLI agent is expected to add links in its own loop.
4. **Added cost per ingest.** Bounded to ≤1 call, gated by the anti-spam heuristic. For zero-cost ingest,
   set `WIKILINK_ENRICH_ENABLED=false`.

---

## 7. Do-NOT list (rejection triggers — any one is a block at PR review)

1. **DO NOT** ask the model to return rewritten page content. The model returns **substitutions only**;
   code applies them (R1 discipline). A whole-doc-rewrite prompt is a reject.
2. **DO NOT** hardcode a backend/model. The call resolves via `resolve_provider_config("ingest")`; no
   provider → skip, never a silent default (I6). An `isinstance`/`provider_type` branch or a literal
   model id is a reject.
3. **DO NOT** make more than one provider call, loop, or retry in the pass (I7). One bounded call,
   capped + timed out, cost logged. A `while`/retry loop is a reject.
4. **DO NOT** edit frontmatter. Wikilinks go into the **body** only; the frontmatter block is preserved
   byte-for-byte (no PyYAML round-trip on write). A frontmatter mutation is a reject (I5).
5. **DO NOT** apply an unvalidated substitution. Target must be a real candidate page, the mention must
   exist in the body, and self-links are dropped (anti-hallucination).
6. **DO NOT** double-wrap an existing `[[…]]` or add more than one link per (page, target)
   (single-mention). Link spam is a reject.
7. **DO NOT** rescan the vault. The candidate set is a bounded indexed `pages.title` read; only the
   modified pages are re-indexed; `data_version` is bumped ONCE for the pass (I1).
8. **DO NOT** call `GraphEngine.recompute()` / FA2 inline. The single `bump_version()` lets the debounced
   cache recompute (I2).
9. **DO NOT** let the pass raise into the ingest critical path — fire-and-forget, try/except, the pages
   are already written and valid (I7 safety posture).
10. **DO NOT** add a new `InferenceProvider` ABC method — reuse `chat()` (operation `"ingest"`), text in /
    substitution JSON out (I6 discipline, as ADR-0024/0034).

---

## 8. Invariant compliance

| Inv | How this design guarantees it |
|-----|-------------------------------|
| **I6** | The enrichment LLM call routes through `resolve_provider_config("ingest", vault_id)` + `resolve_provider` + `InferenceProvider.chat()` — provider-agnostic text/JSON in/out. No hardcoded backend or model; no class-name/`provider_type` branching. "No provider configured" → skip, never a silent default. No new ABC method. |
| **I7** | ≤1 provider call, no loop, no retry; `WIKILINK_ENRICH_MAX_CANDIDATES`/`MAX_SUBS` caps; `token_budget` from the resolved row; `asyncio.wait_for` timeout; anti-spam gate skips trivial runs at zero cost; `total_cost_usd` logged with the `$1` anomaly WARNING. Fire-and-forget failure never runs away or fails the ingest. |
| **I5** | Inserts `[[wikilinks]]` into **bodies only** via the K5 grammar; the frontmatter block is preserved verbatim (no PyYAML write round-trip). `wiki/` stays a valid Obsidian vault; `test_obsidian_check.py` is the gate. |
| **I1** | Candidate titles come from a bounded indexed `pages.title` read (no vault walk); only the modified pages are re-indexed (`persist_metadata`/`upsert_vector`/`persist_links`/`update_index`); `data_version` is bumped ONCE for the pass. |
| **F4** | Each applied substitution becomes a `links` row → the F4 **direct link ×3** edge term → previously-disconnected clusters connect. New edges flow into the layout on the next debounced FA2 recompute (I2 — no inline FA2). |
| **I2** | The single `bump_version()` triggers the debounced GraphCache recompute; enrichment never runs FA2 inline. |
| **I8** | No schema change (no D2). New `WIKILINK_ENRICH_*` env vars documented in DEPLOY.md; the D3 ingest-loop sequence gains the `enrich_wikilinks` step (tech-writer). No new container/component (D1 unchanged — `ops/enrich_wikilinks.py` is a component inside the FastAPI service). |

No invariant is traded. The genuine tensions (over-linking, candidate truncation, CLI-branch exclusion)
are flagged in §6 and bounded/recorded.

---

## 9. Implementation plan

> **Routing rule:** the enrichment LLM call touches `InferenceProvider`, so the provider-call work is
> **[AI]** (ai-agent-engineer, CLAUDE.md §13); the deterministic substitution-apply + incremental
> re-index is **[BE]** (backend-engineer).

- **ai-agent-engineer [AI]:** `enrich_wikilinks` provider call — the structured-substitution prompt, the
  candidate-title-list assembly (bounded), the single bounded `InferenceProvider.chat()` invocation
  (resolve via `resolve_provider_config("ingest")`, I6), JSON parse into `WikilinkSubstitution`, the
  `WIKILINK_ENRICH_*` bounds + timeout + cost logging (I7), and the fire-and-forget failure degradation.
  Add `WIKILINK_ENRICH_ENABLED/MIN_CHARS/MAX_CANDIDATES/MAX_SUBS/TOKEN_BUDGET/TIMEOUT_SECONDS` to
  `config.py`. Tests: anti-spam gate skips on a trivial/empty-candidate run (zero cost); one call,
  capped + timeout-degrades to zero substitutions; no-provider → skip (never hardcode).
- **backend-engineer [BE]:** the deterministic apply (first-occurrence, body-only, no double-wrap,
  single-mention), step-5 validation, the atomic temp+`os.replace` write, and the incremental re-index
  (`persist_metadata`/`upsert_vector`/`parse_wikilinks`+`persist_links`/`update_index`/`bump_version`
  once). Wire the fire-and-forget hook in `run_ingest_pipeline` **before** `propose_reviews`. Tests:
  validated substitutions produce `links` rows + the F4 ×3 edge; an unknown-target/self-link/missing-
  mention substitution is dropped; frontmatter is byte-identical after enrichment;
  `test_obsidian_check.py` green; `data_version` bumped exactly once; only edited pages are re-indexed.
- **tech-writer:** this ADR (format + README row), DEPLOY.md (`WIKILINK_ENRICH_*` env), USER.md (clusters
  now connect via auto-added wikilinks; the human can edit them in the CodeMirror editor), and the D3
  ingest-loop sequence (add the `enrich_wikilinks` step before `propose_reviews`). No D1 topology change.

---

## 10. Sign-off

**APPROVED to implement.** A bounded, provider-agnostic wikilink-enrichment post-pass adopts the R1
substitution-apply pattern: the LLM returns `{mention, target_title}` substitutions, code validates and
applies them **single-mention** into page **bodies only**, and the modified pages are re-indexed
incrementally — restoring the F4 **direct link ×3** signal so concept clusters connect. The call routes
through `InferenceProvider` (I6, no hardcoded backend), is bounded by `max_iter`-equivalent single-call
caps + `token_budget` + timeout with `total_cost_usd` logged (I7), preserves frontmatter (I5), touches
only the edited pages and bumps `data_version` once (I1), and fires the debounced FA2 recompute rather
than running it inline (I2). Fire-and-forget: failure never fails the ingest. No schema change.

> Handoff: ADR-0036 → tech-writer (format, README row, DEPLOY/USER, D3 sequence). Interface contract
> (§2) → ai-agent-engineer [AI] (provider call) + backend-engineer [BE] (apply + re-index). PR verdicts →
> orchestrator.
