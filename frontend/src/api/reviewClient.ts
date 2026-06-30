/**
 * reviewClient.ts — typed API client for the F9 HITL review queue endpoints.
 *
 * GET  /review/queue                        → ReviewQueueResponse (paginated)
 * POST /review/queue/{id}/create            → ReviewItem (201, preferred alias — ADR-0034 §7)
 * POST /review/queue/{id}/approve           → ReviewItem (201, backward-compat path)
 * POST /review/queue/{id}/skip              → ReviewItem (200)
 * POST /review/queue/{id}/deep-research     → ReviewDeepResearchResponse (202)
 * POST /review/queue/sweep                  → ReviewSweepResponse (200)
 *
 * No secrets in this file (CLAUDE.md §12).
 * No provider/model literals hardcoded (I6).
 *
 * ADR-0034 §7, AC-F9-3
 */

import type {
  ReviewQueueResponse,
  ReviewItem,
  ReviewDeepResearchResponse,
  ReviewSweepResponse,
} from "./types";
import { ApiError } from "./graphClient";

const API_BASE: string =
  (import.meta.env["VITE_API_BASE"] as string | undefined) ?? "";

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
 * Fetch paginated HITL review queue proposals.
 * GET /review/queue?vault_id=<vaultId>&limit=<limit>&offset=<offset>
 *
 * vault_id is required by the backend (ADR-0034 §7).
 * Each item is a PROPOSAL in the new projection (ADR-0034 §7.1) — no pre_generated_query.
 */
export async function fetchReviewQueue(
  options: { vaultId: string; limit?: number; offset?: number },
  signal?: AbortSignal,
): Promise<ReviewQueueResponse> {
  const { vaultId, limit = 50, offset = 0 } = options;
  const url = `${API_BASE}/review/queue?vault_id=${encodeURIComponent(vaultId)}&limit=${limit}&offset=${offset}`;
  const res = await fetch(url, signal !== undefined ? { signal } : undefined);
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
  const url = `${API_BASE}/review/queue/${encodeURIComponent(itemId)}/create`;
  const res = await fetch(url, { method: "POST" });
  await checkResponse(res);
  return (await res.json()) as ReviewItem;
}

/**
 * Skip a review item.
 * POST /review/queue/{id}/skip → 200 ReviewItem
 */
export async function skipReviewItem(itemId: string): Promise<ReviewItem> {
  const url = `${API_BASE}/review/queue/${encodeURIComponent(itemId)}/skip`;
  const res = await fetch(url, { method: "POST" });
  await checkResponse(res);
  return (await res.json()) as ReviewItem;
}

/**
 * Trigger deep research for a review item.
 * POST /review/queue/{id}/deep-research → 202 { review_item_id, run_id }
 *
 * 503 if SEARXNG_URL is unset on the backend (I9 guard — no fake run).
 * AC-F10-5: run_id is stored on the review_item row for traceability.
 * Topic is derived from proposed_title → rationale → page.title (ADR-0034 §7, was pre_generated_query).
 */
export async function deepResearchReviewItem(
  itemId: string,
): Promise<ReviewDeepResearchResponse> {
  const url = `${API_BASE}/review/queue/${encodeURIComponent(itemId)}/deep-research`;
  const res = await fetch(url, { method: "POST" });
  await checkResponse(res);
  return (await res.json()) as ReviewDeepResearchResponse;
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
  const url = `${API_BASE}/review/queue/sweep?vault_id=${encodeURIComponent(vaultId)}`;
  const res = await fetch(url, { method: "POST" });
  await checkResponse(res);
  return (await res.json()) as ReviewSweepResponse;
}
