/**
 * researchClient.ts — typed API client for Synapse deep-research endpoints (F10, ADR-0024 §8).
 *
 * POST /research/optimize-topic → OptimizeTopicResponse
 * POST /research/start          → ResearchStartResponse (202)
 * GET  /research/runs           → ResearchRunListResponse
 * GET  /research/runs/{id}      → ResearchRunDetail
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
import { apiBase, apiFetch } from "./base";
// API_BASE removed: use apiBase() at call time (ADR-0047 §2.1/§2.2).

// ─── Topic optimization response ──────────────────────────────────────────────

export interface OptimizeTopicResponse {
  optimized_topic: string;
  queries: string[];
}

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
 * Optimize a raw seed topic via LLM and return a refined topic + initial queries.
 * POST /research/optimize-topic { topic }
 * Returns { optimized_topic, queries }
 *
 * Graceful degradation: when no provider is configured the backend echoes the seed
 * topic and produces naive queries — the dialog will still open and be editable.
 *
 * B5/D3 contract (feat/b5-research).
 */
export async function optimizeResearchTopic(
  topic: string,
  signal?: AbortSignal,
): Promise<OptimizeTopicResponse> {
  const url = `${apiBase()}/research/optimize-topic`;
  const res = await apiFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ topic }),
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
  return (await res.json()) as OptimizeTopicResponse;
}

/**
 * Start a bounded deep-research run.
 * POST /research/start { vault_id, topic, queries?, max_iter?, token_budget? }
 * Returns 202 { run_id } immediately — poll GET /research/runs/{id} for progress.
 *
 * B5/D3: optional `queries` field passes the user-edited queries from the confirm
 * dialog directly, so the backend can seed its first iteration without re-generating.
 * If the backend does not yet support `queries`, it is silently ignored (additive field).
 */
export async function startResearch(
  params: {
    vault_id: string;
    topic: string;
    /** Optional seed queries from the confirm dialog (B5/D3). Ignored by older backends. */
    queries?: string[];
    max_iter?: number;
    token_budget?: number;
  },
  signal?: AbortSignal,
): Promise<ResearchStartResponse> {
  const url = `${apiBase()}/research/start`;
  const res = await apiFetch(url, {
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
  const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);
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
  const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as ResearchRunDetail;
}
