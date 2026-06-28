/**
 * graphClient.ts — typed API client for Synapse graph endpoints.
 *
 * Base URL is read from VITE_API_BASE env var (set in .env.local, never committed).
 * Default: http://localhost:8000
 *
 * No secrets, API keys, or auth tokens live in this file (CLAUDE.md §12).
 *
 * INVARIANT I2: this client fetches PRECOMPUTED coords from GET /graph.
 * It NEVER calls any layout function. The x/y values are passed verbatim
 * to the Zustand store and then to graphology nodes.
 */

import type { CacheStatus, GraphResponse, PageDetail } from "./types";

// ─── Configuration ────────────────────────────────────────────────────────────

/** Backend base URL — configurable via VITE_API_BASE, no trailing slash */
const API_BASE: string =
  (import.meta.env["VITE_API_BASE"] as string | undefined) ?? "http://localhost:8000";

// ─── Errors ───────────────────────────────────────────────────────────────────

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

async function checkResponse(res: Response): Promise<void> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // ignore parse error; use statusText
    }
    throw new ApiError(res.status, `${res.status} ${detail}`);
  }
}

function parseCacheHeader(res: Response): CacheStatus {
  const value = res.headers.get("X-Graph-Cache");
  if (value === "hit") return "hit";
  if (value === "miss") return "miss";
  return "unknown";
}

// ─── Public API ───────────────────────────────────────────────────────────────

export interface FetchGraphResult {
  /** Parsed graph response body (nodes + edges with PRECOMPUTED coords) */
  data: GraphResponse;
  /** Value of the X-Graph-Cache response header */
  cacheStatus: CacheStatus;
}

/**
 * Fetch the graph for a vault from GET /graph?vault_id=<vaultId>.
 *
 * Returns precomputed node coords (x, y) — I2: the client MUST NOT
 * recompute or mutate these coordinates with any layout algorithm.
 *
 * @param vaultId - The vault to fetch. Defaults to "default".
 * @param signal  - Optional AbortSignal for request cancellation.
 */
export async function fetchGraph(
  vaultId: string = "default",
  signal?: AbortSignal,
): Promise<FetchGraphResult> {
  const url = `${API_BASE}/graph?vault_id=${encodeURIComponent(vaultId)}`;
  const res = await fetch(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);

  const cacheStatus = parseCacheHeader(res);
  const data = (await res.json()) as GraphResponse;

  // Dev-mode assertion: coords must be present on every node (I2 contract check)
  if (typeof __DEV__ !== "undefined" && __DEV__) {
    for (const node of data.nodes) {
      console.assert(
        typeof node.x === "number" && typeof node.y === "number",
        "[I2] Server returned node without precomputed coords — layout contract violated",
        node,
      );
    }
  }

  return { data, cacheStatus };
}

/**
 * Fetch page detail for the node-click tooltip.
 * GET /pages/{id}
 */
export async function fetchPageDetail(
  pageId: string,
  signal?: AbortSignal,
): Promise<PageDetail> {
  const url = `${API_BASE}/pages/${encodeURIComponent(pageId)}`;
  const res = await fetch(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as PageDetail;
}
