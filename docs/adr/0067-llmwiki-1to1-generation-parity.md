# ADR-0067 — LLM Wiki 1:1 generation-semantics parity

- **Status:** Partially superseded by ADR-0073 and ADR-0074 (2026-07-13). D2, D5 and D6 remain accepted; D1, D3 and D4 generation exclusivity are superseded as described below.
- **Supersedes/extends:** ADR-0066 (llm_wiki 1:1 parity program), ADR-0058 (lint parity), ADR-0063 (ingest quality parity)
- **Driver:** Live audit `docs/reference/AUDIT-SYNAPSE-VS-LLMWIKI-1TO1-2026-07-10.md` — with the same raw corpus (~87% overlap) and same model class (Haiku 4.5), the Synapse PROD vault (`/Volumes/synapse/vault`) diverges structurally from the LLM Wiki app vault (`~/Documents/00_Personal/01_Wiki/LLM Wiki`): queries 133-stubs vs 110-real, entities 149 vs 202, synthesis/comparison 0/0 vs 4/5, `related:` 0% vs 100%.

## Context

Synapse reproduces the Karpathy page-type model but three generation seams diverge from LLM Wiki. This ADR records the invariant-touching decisions the owner approved so implementation is not a silent workaround (per the CLAUDE.md invariant-change rule).

## Decisions

### D1 — `queries/` is reserved for genuine open questions (fixes QP-Q1/LN-D1)
**Superseded in part by ADR-0073:** lint still may not create query stubs, but source-grounded
direct ingest may now generate a genuine research-query page.
Wiki Lint MUST NOT materialise a missing wikilink target as `type: query`. `_create_broken_link_stub` is replaced with folder-aware routing: a target that resolves to a known type is stubbed in that type's folder; a bare proper-noun target → `entity` stub, otherwise `concept` stub; when uncertain the no-suggestion path routes to the **Review queue (F9)** instead of writing. `query` pages are written ONLY by (a) the contradiction handler (D4) and (b) chat save-to-wiki when the saved content is itself a question. This restores nashsu parity (LLM Wiki `queries/` = 0 lint stubs).

### D2 — Frontmatter mirrors LLM Wiki; provenance kept in the DB (owner choice "mirror exactly", resolves Q1/FW-D1/FW-D5/FW-D4/QP-Q3)
On-disk generated-page frontmatter is emitted in LLM Wiki's shape and key order:
`type, title, created, updated, tags, related` (+ `authors, year, url, venue` on sources). Serializer switches to `sort_keys=False`.
- **`related: list[str]`** becomes a first-class field (slugs), populated from resolved outbound wikilinks + top graph neighbours (cap ~8). It is a **second graph-edge seed** alongside `[[wikilinks]]`, but only *resolvable* slugs are emitted (an unresolved `related` entry is dropped, never a new ghost).
- **`sources` and `lang` are no longer emitted in the markdown** and become **optional** on `WikiFrontmatter` (default `[]`/`"en"`). **F3 traceability is preserved in Postgres**: the ingest pipeline still populates `pages.sources`/links (the graph source-overlap ×4 signal F4 and cascade-delete F13 read the DB, not the file). This is the documented F3 amendment: traceability moves from an inline `sources:` path list to (a) the DB and (b) the `source` page + `related:` links, exactly as LLM Wiki expresses it.
- Overview and lint-stub pages may carry `sources: []` (no synthetic `lint:<uuid>` sources).

### D3 — Corpus-level synthesis/comparison generator (fixes SC-D1; resolves Q3)
**Superseded by ADR-0073/0074:** direct ingest may generate source-local special pages; the global
corpus pass remains separate but requires a real domain, indexed generation keys and independent
candidate bounds.
The ingest-time prohibition on synthesis/comparison (`_common.py`) STAYS. A new bounded op `ops/synthesize.py` runs after bulk import (and as an explicit action): it seeds candidate clusters from the 4-signal graph (source-overlap/type-affinity/Adamic-Adar), generates a synthesis (thesis+integration) per high-confidence cluster and a comparison table for ≥2 same-class entities, writing them via the single `write_wiki_page` seam with `related[]`=cluster + unioned `sources[]`. Borderline clusters → Review (F9). Bounded by `max_iter`+`token_budget`; logs `total_cost_usd` (I7). These are legitimate writers of synthesis/comparison, distinct from the still-prohibited single-doc ingest.

### D4 — Contradiction → open-question query (fixes QP-Q2/IN-D2; resolves Q3)
**Superseded in exclusivity by ADR-0073:** the human-gated contradiction path remains valid but is
no longer the only ingest-adjacent producer of query pages.
`contradiction` leaves `_FLAG_ONLY_CATEGORIES`; an applied contradiction authors a `type: query` page via an internal pipeline writer (not free provider output): question title + `## Question / ## Hypothesis / ## Open Points / ## Impact / ## References`, `sources[]`=both raw docs, `related[]`=both pages. Human-gated (K8); confidence-gated; bounded (I7). `query` stays provider-forbidden as free output; only the internal writer may emit it.

### D5 — Entity canonicalisation (fixes CE-D1/IN-D3; resolves Q5)
`write_wiki_page` resolves a normalized identity key for `type=entity` **before** slugging (casefold; strip parenthetical acronyms `(AWS)`; strip legal suffixes `Inc./Ltd./S.p.A./PRIVATE LIMITED`; fold known acronym↔longform). Exact normalized-key match → reuse the existing page (union `sources[]`, merge body). Anything requiring fuzzy/embedding similarity → Review proposal, **never a silent merge** (`Deloitte` vs `Deloitte Italia` stays human-decided). A retrofit sweep `ops/dedup_entities.py` clusters the existing vault and proposes merges to Review. Ingest prompt + `schema.md` gain a canonical-naming rule.

### D6 — Structure & catalogue (fixes IL/OV): index dedup + em-dash gloss + `## Queries` (not `Querys`) + drop `## Uncategorised`; overview gains a deterministic `## Open Questions` block (DB query of live `query` pages) + a ≥100-keyword tag cloud + a bolded thesis; log becomes coarse per-document narrative (one `- Ingest: <doc> — created N pages` bullet) instead of per-page machine lines. All deterministic (no live LLM in index/log) to preserve idempotency (I1/K3/K4).

## Invariant impact (explicit)
- **F3** amended (D2): inline `sources:` no longer required in file frontmatter; traceability preserved in DB + source pages. Reversible (re-enable emission).
- **F4** extended (D2): `related:` is a second edge seed (resolvable slugs only).
- **I1/I5/I7** unchanged: every new op is incremental (single `data_version` bump/batch), Obsidian-valid, bounded + cost-logged.
- **K8/F9** strengthened: entity merges and contradiction questions are human-gated.
- The former single-doc ingest prohibition was superseded by ADR-0073. Source-grounding and
  bounded-generation tests replace the inverse prohibition tests.

## Validation
Per audit §5: fixture/unit gates + live re-run of ingest/dedup/reclassify/contradiction/synthesize/overview/chat/graph against a **reloaded dev KB** (PROD untouched), scored against the acceptance table. `scripts/parity_report.py` greps both vaults and prints the scorecard.
