/**
 * reviewClient.ts — typed API client for the F9 HITL review queue endpoints.
 *
 * GET  /review/queue                        → ReviewQueueResponse (paginated)
 * POST /review/queue/{id}/create            → ReviewItem (201, preferred alias — ADR-0034 §7)
 * POST /review/queue/{id}/approve           → ReviewItem (201, backward-compat path)
 * POST /review/queue/{id}/skip              → ReviewItem (200)
 * POST /review/queue/{id}/dismiss           → ReviewItem (200, ADR-0044 §6)
 * POST /review/queue/{id}/deep-research     → ReviewDeepResearchResponse (202)
 * POST /review/queue/bulk                   → ReviewBulkResponse (200, ADR-0044 §6)
 * POST /review/queue/sweep                  → ReviewSweepResponse (200)
 * DELETE /review/queue/resolved             → ReviewClearResolvedResponse (200, ADR-0044 §6)
 *
 * No secrets in this file (CLAUDE.md §12).
 * No provider/model literals hardcoded (I6).
 *
 * ADR-0034 §7, ADR-0044 §6, AC-F9-3
 */

import type {
  ReviewQueueResponse,
  ReviewItem,
  ReviewDeepResearchResponse,
  ReviewSweepResponse,
  ReviewBulkRequest,
  ReviewBulkResponse,
  ReviewClearResolvedResponse,
} from "./types";
import { ApiError } from "./graphClient";
import { apiBase, apiFetch } from "./base";
// API_BASE removed: use apiBase() at call time (ADR-0047 §2.1/§2.2).

async function checkResponse(res: Response): Promise<void> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // ignore parse error
    }
    throw new ApiError(res.status, `${res.status} ${detail}`);
  }
}

/**
 * Status filter for GET /review/queue (ADR-0044 §6).
 * "pending" = live pending items (default).
 * "resolved" = terminal-resolved set (created / auto_resolved / deep_researched).
 * "dismissed" = dismissed-only set.
 * "all" = all statuses, no filter.
 */
export type ReviewQueueStatus = "pending" | "resolved" | "dismissed" | "all";

/**
 * Fetch paginated HITL review queue proposals.
 * GET /review/queue?vault_id=<vaultId>&status=<status>&limit=<limit>&offset=<offset>
 *
 * vault_id is required by the backend (ADR-0034 §7).
 * status defaults to "pending" (ADR-0044 §6).
 * Each item is a PROPOSAL in the enriched projection (ADR-0044 §6.1).
 */
export async function fetchReviewQueue(
  options: {
    vaultId: string;
    status?: ReviewQueueStatus;
    limit?: number;
    offset?: number;
  },
  signal?: AbortSignal,
): Promise<ReviewQueueResponse> {
  const { vaultId, status = "pending", limit = 50, offset = 0 } = options;
  const url = `${apiBase()}/review/queue?vault_id=${encodeURIComponent(vaultId)}&status=${encodeURIComponent(status)}&limit=${limit}&offset=${offset}`;
  const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as ReviewQueueResponse;
}

/**
 * Create action: lazy on-demand page generation from a proposal.
 * POST /review/queue/{id}/create → 201 ReviewItem (preferred explicit verb — ADR-0034 §7)
 *
 * This runs a bounded LLM generation call that can take many seconds.
 * 201 on success (page written, data_version bumped — I1).
 * 409 if item is not pending or no ingest provider configured (I6).
 * 502 if page generation fails; item is left pending — show "retry or skip" in the UI.
 *
 * Note: returns 502 until ai-agent-engineer lands the generation seam (ADR-0034 §11.2).
 * The UI must handle 502 gracefully and keep the item in the list.
 */
export async function createReviewItem(itemId: string): Promise<ReviewItem> {
  const url = `${apiBase()}/review/queue/${encodeURIComponent(itemId)}/create`;
  const res = await apiFetch(url, { method: "POST" });
  await checkResponse(res);
  return (await res.json()) as ReviewItem;
}

/**
 * Skip a review item.
 * POST /review/queue/{id}/skip → 200 ReviewItem
 */
export async function skipReviewItem(itemId: string): Promise<ReviewItem> {
  const url = `${apiBase()}/review/queue/${encodeURIComponent(itemId)}/skip`;
  const res = await apiFetch(url, { method: "POST" });
  await checkResponse(res);
  return (await res.json()) as ReviewItem;
}

/**
 * Dismiss a review item (ADR-0044 §6).
 * POST /review/queue/{id}/dismiss → 200 ReviewItem
 *
 * Distinct from skip: dismissed = "hide this, not acting"; skipped = "considered and declined".
 * Both are terminal; both cleared by clearResolved.
 */
export async function dismissReviewItem(itemId: string): Promise<ReviewItem> {
  const url = `${apiBase()}/review/queue/${encodeURIComponent(itemId)}/dismiss`;
  const res = await apiFetch(url, { method: "POST" });
  await checkResponse(res);
  return (await res.json()) as ReviewItem;
}

/**
 * Trigger deep research for a review item.
 * POST /review/queue/{id}/deep-research → 202 { review_item_id, run_id }
 *
 * ADR-0044: topic now seeds from search_queries[0] when present (fallback: ADR-0034 order).
 * 503 if SEARXNG_URL is unset on the backend (I9 guard — no fake run).
 * AC-F10-5: run_id is stored on the review_item row for traceability.
 */
export async function deepResearchReviewItem(
  itemId: string,
): Promise<ReviewDeepResearchResponse> {
  const url = `${apiBase()}/review/queue/${encodeURIComponent(itemId)}/deep-research`;
  const res = await apiFetch(url, { method: "POST" });
  await checkResponse(res);
  return (await res.json()) as ReviewDeepResearchResponse;
}

/**
 * Bulk action on a set of review items (ADR-0044 §6).
 * POST /review/queue/bulk → 200 ReviewBulkResponse
 *
 * actions: "skip" | "dismiss" | "mark-resolved"
 * Bounded: len(ids) ≤ REVIEW_BULK_MAX_IDS (default 200 server-side); 400 if exceeded (I7).
 * Only pending ids are mutated; already-terminal ids → skipped_terminal (never re-mutated).
 * No provider call — pure bounded DB write.
 */
export async function bulkReview(request: ReviewBulkRequest): Promise<ReviewBulkResponse> {
  const url = `${apiBase()}/review/queue/bulk`;
  const res = await apiFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  await checkResponse(res);
  return (await res.json()) as ReviewBulkResponse;
}

/**
 * Trigger the manual auto-resolution sweep.
 * POST /review/queue/sweep?vault_id=<vaultId> → 200 ReviewSweepResponse
 *
 * Pass-1: rule-based title match (no LLM). Pass-2: conservative bounded LLM (I6/I7).
 * Idempotent. Bounded. Does not fail if no items qualify (ADR-0034 §6).
 */
export async function sweepReviewQueue(
  vaultId: string,
): Promise<ReviewSweepResponse> {
  const url = `${apiBase()}/review/queue/sweep?vault_id=${encodeURIComponent(vaultId)}`;
  const res = await apiFetch(url, { method: "POST" });
  await checkResponse(res);
  return (await res.json()) as ReviewSweepResponse;
}

/**
 * Clear all resolved/terminal rows for a vault (ADR-0044 §6).
 * DELETE /review/queue/resolved?vault_id=<vaultId> → 200 ReviewClearResolvedResponse
 *
 * Hard-deletes terminal rows (skipped / dismissed / created / auto_resolved / deep_researched).
 * Pending rows are NEVER touched (ADR-0044 §10 Do-NOT #6).
 * Bounded, vault-scoped, idempotent.
 */
export async function clearResolved(
  vaultId: string,
): Promise<ReviewClearResolvedResponse> {
  const url = `${apiBase()}/review/queue/resolved?vault_id=${encodeURIComponent(vaultId)}`;
  const res = await apiFetch(url, { method: "DELETE" });
  await checkResponse(res);
  return (await res.json()) as ReviewClearResolvedResponse;
}

/**
 * Single-item approve/resolve (R2 — "Approve" action for confirm + contradiction types).
 * Reuses POST /review/queue/bulk with action="mark-resolved" and a single item id.
 * No new backend endpoint — the bulk endpoint already supports single-id lists.
 * vault_id is required by the bulk endpoint for logging/scoping.
 *
 * On success: item transitions to auto_resolved (terminal), appears in "Resolved" tab.
 * The UI removes the item from the pending list optimistically.
 */
export async function resolveReviewItem(
  itemId: string,
  vaultId: string,
): Promise<ReviewBulkResponse> {
  return bulkReview({ vault_id: vaultId, action: "mark-resolved", ids: [itemId] });
}
