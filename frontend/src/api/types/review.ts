/**
 * HITL review queue API contract types (F9, FE-QUAL-11 split of api/types.ts).
 * GET /review/queue · POST /review/queue/{id}/… (ADR-0034 §7.1, ADR-0044)
 */

import type { PageType } from "./pages";

/**
 * Five proposal types (ADR-0034 §3.1).
 * Old values (new_page / update_page / deep_research_candidate) are gone.
 */
export type ReviewItemType =
  | "missing-page"
  | "suggestion"
  | "contradiction"
  | "duplicate"
  | "confirm"
  | "purpose-suggestion"
  | "schema-suggestion";

/** Generator that produced a review proposal (v1.6 additive provenance contract). */
export type ReviewProposalOrigin = "rule" | "ai" | "corpus" | "system" | "lint" | "legacy";

/**
 * Item lifecycle (ADR-0034 §3.1 + ADR-0044 §3.1).
 * "approved" is gone — Create produces "created".
 * "dismissed" added in ADR-0044: human hid the item without acting.
 */
export type ReviewItemStatus =
  "pending" | "created" | "skipped" | "dismissed" | "deep_researched" | "auto_resolved";

/**
 * Convenience projection of a referenced page in a ReviewItem card.
 * Returned in the referenced_pages convenience join (ADR-0044 §6.1).
 */
export interface ReviewReferencedPage {
  id: string;
  title: string;
  type: string | null;
}

/**
 * One review_items row as returned by the API (ADR-0034 §7.1 + ADR-0044 §6.1).
 *
 * The item is a PROPOSAL: proposed_title + rationale describe what the LLM
 * recommends creating or investigating. The Create action lazily generates the
 * page on-demand (ADR-0034 §5); Deep Research and Skip close without writing.
 *
 * page_title is a convenience join from pages.title for the page_id FK (the
 * conflicting/context page for contradiction/duplicate types).
 *
 * pre_generated_query is REMOVED — superseded by rationale + the suggestion type.
 *
 * ADR-0044 additions:
 *   content_key          — stable FNV-1a dedup handle (opaque to UI)
 *   referenced_page_ids  — array of existing page-id strings the proposal is about
 *   referenced_pages     — [{id, title, type}] convenience join for the card (no extra round-trip)
 *   search_queries       — ≤3 pre-generated web-search query strings (Deep-Research seeds)
 */
export interface ReviewItem {
  id: string;
  vault_id: string;

  /** Proposal type (ADR-0034 §3.1): missing-page | suggestion | contradiction | duplicate | confirm */
  item_type: ReviewItemType;

  /** Generator provenance. Absent/null only on rows returned by pre-v1.6 backends. */
  proposal_origin?: ReviewProposalOrigin | null;

  /** Item lifecycle: pending | created | skipped | dismissed | deep_researched | auto_resolved */
  status: ReviewItemStatus;

  /** Title the LLM proposes to create (required for missing-page; advisory for others). */
  proposed_title: string | null;

  /** Inferred PageType for the lazy skeleton: entity | concept | source | synthesis | comparison */
  proposed_page_type: string | null;

  /** Actual type of the page produced by Create, when the proposal is resolved. */
  created_page_type?: PageType | null;

  /** Target wiki/ subdir (display only — recomputed at Create from the final type). */
  proposed_dir: string | null;

  /** Short human-readable "why this matters" (replaces the old follow-up questions). */
  rationale: string | null;

  /**
   * Review TARGET: the existing page a contradiction/duplicate conflicts with,
   * or the source-context page for a missing-page/suggestion. null when none applies.
   */
  page_id: string | null;

  /** Convenience join from pages.title for page_id (UI display). */
  page_title: string | null;

  /** Provenance: the page whose ingest produced this proposal. */
  source_page_id: string | null;

  /** Page produced by a successful Create action; null otherwise. */
  created_page_id: string | null;

  /** How the item closed: created | skipped | dismissed | researched | rule_resolved | llm_resolved. null while pending. */
  resolution: string | null;

  /** Set when the Deep-Research action fires (AC-F10-5); null otherwise. */
  deep_research_run_id: string | null;

  /**
   * ADR-0044 §6.1: stable FNV-1a content-derived dedup handle (opaque string or null).
   * null for confirm items (never deduped) and legacy rows.
   */
  content_key: string | null;

  /**
   * ADR-0044 §6.1: array of existing page-id strings this proposal is contextually about.
   * null/[] when none. Distinct from page_id (single primary conflict).
   * May contain stale ids if referenced pages were deleted — filtered at render time.
   */
  referenced_page_ids: string[] | null;

  /**
   * ADR-0044 §6.1: convenience join — [{id, title, type}] for referenced_page_ids.
   * Populated by the backend (bounded pages lookup); stale ids are already filtered.
   * null when referenced_page_ids is null/[].
   */
  referenced_pages: ReviewReferencedPage[] | null;

  /**
   * ADR-0044 §6.1: ≤3 pre-generated web-search query strings.
   * Shown on the card as "will search: …"; first entry seeds Deep Research.
   * null when the model produced none or for rule-based proposals.
   */
  search_queries: string[] | null;

  created_at: string; // ISO-8601
  reviewed_at: string | null;
}

export interface ReviewQueueResponse {
  items: ReviewItem[];
  total: number;
  limit: number;
  offset: number;
}

/** 202 response for POST /review/queue/{id}/deep-research */
export interface ReviewDeepResearchResponse {
  review_item_id: string;
  run_id: string;
}

/** 200 response for POST /review/queue/sweep (ADR-0034 §7) */
export interface ReviewSweepResponse {
  rule_resolved: number;
  llm_resolved: number;
  kept: number;
}

/**
 * Request body for POST /review/queue/bulk (ADR-0044 §6).
 * Bounded: len(ids) ≤ REVIEW_BULK_MAX_IDS (default 200) — 400 otherwise (I7).
 * Only pending ids are mutated; already-terminal ids are counted in skipped_terminal.
 */
export interface ReviewBulkRequest {
  vault_id: string;
  action: "skip" | "dismiss" | "mark-resolved";
  ids: string[];
}

/** 200 response for POST /review/queue/bulk (ADR-0044 §6) */
export interface ReviewBulkResponse {
  updated: number;
  skipped_terminal: number;
}

/** 200 response for DELETE /review/queue/resolved (ADR-0044 §6) */
export interface ReviewClearResolvedResponse {
  deleted: number;
}
