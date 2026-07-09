# ADR-0064 — missing-page review item fan-out to multiple pages

**Status:** Accepted  
**Date:** 2026-07-09  
**Feature IDs:** F9, K8  
**Sprint:** v1.3.13 (llm_wiki parity)

---

## 1. Context

The nashsu/llm_wiki reference (`src/lib/review-create-page.ts`) allows a single
`missing-page` review item to fan out into **multiple wiki pages** when the `proposed_title`
encodes a comma- / 、- / " and "-separated list (e.g. "Galaxy Formation, Star Formation,
and Nebulae" → three pages). This is the `extractMissingPageCandidates` pattern (line 34).

In Synapse v1.3.12 and earlier, `create_page_from_review` produced exactly ONE page per
review item, regardless of how many concepts the proposed title listed. This was a parity
gap (R1).

---

## 2. Decision

### 2a. Fan-out for `missing-page` only

`_extract_missing_page_candidates(proposed_title: str) -> list[str]` splits on:

| Delimiter | Description |
|-----------|-------------|
| `,` | ASCII comma |
| `，` | CJK fullwidth comma |
| `、` | Japanese ideographic comma |
| `;` | ASCII semicolon |
| `；` | CJK fullwidth semicolon |
| ` and ` | English conjunction (whole-word, case-insensitive) |
| ` e ` | Italian conjunction (whole-word, case-insensitive) |

After splitting: whitespace-strip; drop empties; deduplicate case-insensitively
(first-seen casing preserved). Cap at **5** candidates (`_MISSING_PAGE_FANOUT_CAP`, I7).

If the split yields ≤1 usable candidate, `[proposed_title]` is returned unchanged —
preserving existing single-page Create behavior with zero regression.

### 2b. Fan-out loop in `create_page_from_review`

For `missing-page` items, `create_page_from_review` iterates over the candidate list and
calls `_run_generation` + write path for **each** candidate:

- **Primary (first) candidate failure** → 502, item left pending (identical to
  pre-fan-out behavior).
- **Secondary candidate failure** → log warning, skip, continue. The primary is already
  committed.
- Each write is exactly one `data_version` bump (I1 — no batch write).

All other item types (`suggestion`, `contradiction`, `duplicate`, `confirm`,
`purpose-suggestion`, `schema-suggestion`) remain on the single-page path.

### 2c. API compatibility preserved (I8)

`created_page_id` on the review item is set to the **first/primary** created page ID.
The 201 response shape (`ReviewItemResponse`) is unchanged — no new required fields.
Existing OpenAPI clients and tests are unaffected.

---

## 3. Lint/review queue separation (K8)

Synapse keeps lint findings **OUT** of the review queue. The explicit `send-to-review`
bridge in lint.py already provides equivalent capability — the human can escalate any lint
finding to a review item when needed. Routing all lint findings into the queue automatically
(as the reference does) adds noise and dilutes the human curation signal (K8). This is a
deliberate divergence from nashsu/llm_wiki. `_extract_missing_page_candidates` is therefore
applied only at Create time, not at lint time.

---

## 4. Alternatives considered

| Alternative | Rejected because |
|-------------|-----------------|
| Create one page with a composite title | Loses granularity; Obsidian wikilinks would not resolve (I5) |
| Fan-out at enqueue time (multiple items) | Changes the contract for callers that enqueue a single item; breaks idempotency key (ADR-0044 §3.2) |
| Expose `created_page_ids[]` in response | Breaks existing OpenAPI clients; the primary ID is sufficient for the UI to navigate |
| Lint→review auto-bridge | Noisy; K8 requires human curation, not automation (see §3) |

---

## 5. Consequences

- `_extract_missing_page_candidates` is a pure function with no I/O — straightforward to
  test in isolation.
- `_MISSING_PAGE_FANOUT_CAP = 5` bounds provider call volume (I7); a pathological title
  with 20 commas generates at most 5 provider calls.
- The fan-out is transparent to the UI: `created_page_id` still points to the primary;
  the user sees the first page opened immediately and finds the siblings in the vault.
- No migration needed: the `review_items` schema is unchanged.
