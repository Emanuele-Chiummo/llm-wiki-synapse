/**
 * Lint API contract types (K2, FE-QUAL-11 split of api/types.ts).
 * POST /lint/scan · GET /lint/runs · GET /lint/findings (ADR-0037 §6)
 */

import type { CascadeDeleteResult } from "./cascade";

/** Lint finding categories (ADR-0037 §6, B1-L1). */
export type LintCategory =
  | "orphan-page"
  | "missing-xref"
  | "contradiction"
  | "stale-claim"
  | "missing-page"
  /** B1-L1: deterministic category derived from links.dangling=True. Zero LLM cost. */
  | "broken-wikilink"
  /** L1 (v1.3.13): deterministic — a page with zero outgoing wikilinks. Zero LLM cost. */
  | "no-outlinks"
  /** L2 (v1.3.13): semantic — a question or source worth adding to the wiki. */
  | "suggestion";

/** Finding lifecycle. */
export type LintFindingStatus = "open" | "applied" | "dismissed";

/** Severity levels. */
export type LintSeverity = "info" | "warning" | "error";

/** Lint run status. */
export type LintRunStatus = "running" | "completed" | "error";

/**
 * Categories that are FLAG-ONLY: the apply endpoint acknowledges but does not
 * rewrite any wiki file. The UI should label the action "Acknowledge" instead of
 * "Fix" and show no file-write expectation.
 *
 * NOTE: "broken-wikilink", "orphan-page" and "no-outlinks" are NOT in this set —
 * each has a real Fix when a suggestion is present (v1.3.13, ADR-0058 §L4):
 *   broken-wikilink + suggested_target → rewrite the dangling [[link]] (or create a
 *     stub page when no target); orphan-page + suggested_target → append a
 *     [[backlink]] into the suggested source page; no-outlinks + suggested_target →
 *     append [[target]] under ## Related. When the suggestion is absent the row is
 *     acknowledge-only. That distinction is made at render time using suggested_target,
 *     not via this set.
 */
export const LINT_FLAG_ONLY_CATEGORIES = new Set<LintCategory>([
  "contradiction",
  "stale-claim",
  // L2 (v1.3.13): semantic suggestion — advisory, no safe automatic edit.
  "suggestion",
]);

/**
 * One lint_findings row (ADR-0037 §6, B1-L2).
 * proposed_action is null for flag-only categories.
 * suggested_target / suggested_page_id added for broken-wikilink (B1-L2): the
 * tolerant resolver computed the best-match existing page at scan time.
 */
export interface LintFinding {
  id: string;
  lint_run_id: string;
  vault_id: string;
  category: LintCategory;
  severity: LintSeverity;
  target_page_id: string | null;
  target_title: string | null;
  description: string;
  proposed_action: string | null;
  status: LintFindingStatus;
  resolution_note: string | null;
  created_at: string; // ISO-8601
  reviewed_at: string | null; // ISO-8601
  /** B1-L2: for broken-wikilink, the suggested existing page title to rewrite to. */
  suggested_target: string | null;
  /** B1-L2: for broken-wikilink, the FK to the suggested existing page. */
  suggested_page_id: string | null;
}

/** Paginated response from GET /lint/findings. */
export interface LintFindingListResponse {
  items: LintFinding[];
  total: number;
  limit: number;
  offset: number;
  /**
   * L11: True per-severity totals for the current status/category view.
   * Present when the backend supports ADR-0037 §6 L11 (v0.6+).
   * Absent on older servers — UI falls back to the loaded-row count per severity.
   */
  severity_totals?: {
    error?: number;
    warning?: number;
    info?: number;
  };
}

/**
 * One lint_runs row (ADR-0037 §6).
 */
export interface LintRun {
  id: string;
  vault_id: string;
  status: LintRunStatus;
  max_iter: number;
  token_budget: number;
  iterations_used: number;
  findings_count: number;
  total_cost_usd: number;
  started_at: string; // ISO-8601
  completed_at: string | null; // ISO-8601
  error_message: string | null;
  created_at: string; // ISO-8601
}

/** Paginated response from GET /lint/runs. */
export interface LintRunListResponse {
  items: LintRun[];
  total: number;
  limit: number;
  offset: number;
}

/**
 * Response from POST /lint/scan (ADR-0037 §6).
 * Returns the run row AND its findings in one call.
 */
export interface LintScanResponse {
  run: LintRun;
  findings: LintFinding[];
}

/** Request body for POST /lint/scan and POST /lint/scan/start. */
export interface LintScanRequest {
  vault_id: string;
  max_iter?: number | null;
  token_budget?: number | null;
  /**
   * L8: when false the paid LLM pass is skipped. Sent in the BODY — it was previously
   * only ever sent as a query param, which the body-model endpoint ignored, so the
   * "Semantic (LLM)" toggle silently had no effect.
   */
  semantic?: boolean;
}

/**
 * 202 response from POST /lint/scan/start — the background twin of POST /lint/scan.
 * The scan is still running when this returns; poll GET /lint/runs/{run_id}.
 */
export interface LintScanStartResponse {
  run_id: string;
  status: string;
  max_iter: number;
  token_budget: number;
  semantic: boolean;
}

// ─── B1: Batch action + page-delete (B1-L5, B1-L9) ──────────────────────────

/** One result entry in a POST /lint/findings/batch response. */
export interface LintBatchResultEntry {
  id: string;
  status: "ok" | "error";
  detail?: string | null;
}

/**
 * Response from POST /lint/findings/batch (B1-L5).
 * Bounded: ≤200 ids per call (I7).
 */
export interface LintBatchResponse {
  results: LintBatchResultEntry[];
  ok_count: number;
  error_count: number;
}

/**
 * Response from DELETE /pages/{page_id} (B1-L9).
 * Matches the existing CascadeDeleteResult shape used by cascade-delete preview.
 * Re-exported here as a convenience alias so lint code can import it without
 * pulling in the cascade-delete types.
 */
export type LintDeletePageResponse = CascadeDeleteResult;
