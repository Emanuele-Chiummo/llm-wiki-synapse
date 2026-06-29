/**
 * reviewClient.ts — typed API client for the F9 HITL review queue endpoints.
 *
 * GET  /review/queue                        → ReviewQueueResponse (paginated)
 * POST /review/queue/{id}/approve           → ReviewItem (200)
 * POST /review/queue/{id}/skip              → ReviewItem (200)
 * POST /review/queue/{id}/deep-research     → ReviewDeepResearchResponse (202)
 *
 * No secrets in this file (CLAUDE.md §12).
 * No provider/model literals hardcoded (I6).
 *
 * ADR-0025 §3.5, AC-F9-3
 */

import type {
  ReviewQueueResponse,
  ReviewItem,
  ReviewDeepResearchResponse,
} from "./types";
import { ApiError } from "./graphClient";

const API_BASE: string =
  (import.meta.env["VITE_API_BASE"] as string | undefined) ?? "http://localhost:8000";

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
 * Fetch paginated HITL review queue items.
 * GET /review/queue?vault_id=<vaultId>&limit=<limit>&offset=<offset>
 *
 * vault_id is required by the backend (ADR-0025 §3.5).
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
 * Approve a review item (status write only — does NOT re-trigger ingest).
 * POST /review/queue/{id}/approve → 200 ReviewItem
 *
 * AC-F9-6: approve is a human confirmation only; no ingest is triggered (I1).
 */
export async function approveReviewItem(itemId: string): Promise<ReviewItem> {
  const url = `${API_BASE}/review/queue/${encodeURIComponent(itemId)}/approve`;
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
 */
export async function deepResearchReviewItem(
  itemId: string,
): Promise<ReviewDeepResearchResponse> {
  const url = `${API_BASE}/review/queue/${encodeURIComponent(itemId)}/deep-research`;
  const res = await fetch(url, { method: "POST" });
  await checkResponse(res);
  return (await res.json()) as ReviewDeepResearchResponse;
}
