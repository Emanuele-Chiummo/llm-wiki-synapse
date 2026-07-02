/**
 * researchClient.ts — typed API client for Synapse deep-research endpoints (F10, ADR-0024 §8).
 *
 * POST /research/start       → ResearchStartResponse (202)
 * GET  /research/runs        → ResearchRunListResponse
 * GET  /research/runs/{id}   → ResearchRunDetail
 *
 * No secrets in this file (CLAUDE.md §12).
 * No provider/model literals hardcoded (I6).
 */

import type {
  ResearchStartResponse,
  ResearchRunListResponse,
  ResearchRunDetail,
} from "./types";
import { ApiError } from "./graphClient";
import { apiBase } from "./base";
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
 * Start a bounded deep-research run.
 * POST /research/start { vault_id, topic, max_iter?, token_budget? }
 * Returns 202 { run_id } immediately — poll GET /research/runs/{id} for progress.
 */
export async function startResearch(
  params: {
    vault_id: string;
    topic: string;
    max_iter?: number;
    token_budget?: number;
  },
  signal?: AbortSignal,
): Promise<ResearchStartResponse> {
  const url = `${apiBase()}/research/start`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
  return (await res.json()) as ResearchStartResponse;
}

/**
 * Fetch paginated deep-research run history.
 * GET /research/runs?limit=<limit>&offset=<offset>[&vault_id=<vaultId>]
 */
export async function fetchResearchRuns(
  options: { limit?: number; offset?: number; vaultId?: string } = {},
  signal?: AbortSignal,
): Promise<ResearchRunListResponse> {
  const { limit = 20, offset = 0, vaultId } = options;
  let url = `${apiBase()}/research/runs?limit=${limit}&offset=${offset}`;
  if (vaultId) url += `&vault_id=${encodeURIComponent(vaultId)}`;
  const res = await fetch(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as ResearchRunListResponse;
}

/**
 * Fetch the full detail for a single deep-research run.
 * GET /research/runs/{id}
 * Returns synthesis_text (null while running), sources list, queries_used, etc.
 */
export async function fetchResearchRunDetail(
  runId: string,
  signal?: AbortSignal,
): Promise<ResearchRunDetail> {
  const url = `${apiBase()}/research/runs/${encodeURIComponent(runId)}`;
  const res = await fetch(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as ResearchRunDetail;
}
