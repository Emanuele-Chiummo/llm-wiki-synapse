/**
 * graphClient.ts — typed API client for Synapse graph endpoints.
 *
 * Base URL is read from VITE_API_BASE env var (set in .env.local, never committed).
 * Default: "" (relative, proxied in dev / same-origin in prod)
 *
 * No secrets, API keys, or auth tokens live in this file (CLAUDE.md §12).
 *
 * INVARIANT I2: this client fetches PRECOMPUTED coords from GET /graph.
 * It NEVER calls any layout function. The x/y values are passed verbatim
 * to the Zustand store and then to graphology nodes.
 */

import type { CacheStatus, GraphResponse, PageDetail } from "./types";
import { apiBase, apiFetch } from "./base";

// ─── Configuration ────────────────────────────────────────────────────────────
// API_BASE removed: use apiBase() at call time (ADR-0047 §2.1/§2.2).

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
  const url = `${apiBase()}/graph?vault_id=${encodeURIComponent(vaultId)}`;
  const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);
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
  const url = `${apiBase()}/pages/${encodeURIComponent(pageId)}`;
  const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as PageDetail;
}

/**
 * Persist a node's new position after user drag.
 * PATCH /pages/{pageId}/position  { x, y }
 *
 * Fire-and-forget from the caller's perspective — errors are logged but not
 * surfaced to the user (the local sigma graph already holds the new position).
 *
 * INVARIANT I2: this writes the user-chosen position back to the server so it
 * survives the next GET /graph refresh. The client does NOT compute layout —
 * it only persists what the user explicitly dragged.
 *
 * @param pageId - Node UUID to update.
 * @param x      - New graph-space x coordinate.
 * @param y      - New graph-space y coordinate.
 * @param signal - Optional AbortSignal for cancellation.
 */
export async function patchNodePosition(
  pageId: string,
  x: number,
  y: number,
  signal?: AbortSignal,
): Promise<void> {
  const url = `${apiBase()}/pages/${encodeURIComponent(pageId)}/position`;
  const res = await apiFetch(url, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ x, y }),
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
}

/** Response body for POST /links/reresolve. */
export interface ReresolveLinksResult {
  /** Number of previously-dangling wikilinks reconnected to a live page. */
  reconnected: number;
  /** Number of wikilinks still dangling after the pass. */
  remaining_dangling: number;
}

/**
 * Re-resolve dangling wikilinks against current pages (POST /links/reresolve).
 *
 * Reconnects historical cross-ingest [[wikilinks]] whose target now matches a live
 * page (tolerant matcher: exact → case-insensitive → slug). When anything reconnects,
 * the backend bumps data_version once so the server-side FA2 layout recomputes with the
 * new edges (I2). This is the "Regenerate graph" action's backend call.
 *
 * INVARIANT I2: the client never computes layout — it only asks the server to reconnect
 * links + recompute, then refetches the precomputed coords via fetchGraph().
 *
 * @param signal - Optional AbortSignal for cancellation.
 */
export async function reresolveLinks(signal?: AbortSignal): Promise<ReresolveLinksResult> {
  const url = `${apiBase()}/links/reresolve`;
  const res = await apiFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
  return (await res.json()) as ReresolveLinksResult;
}

/** Response body for POST /graph/recompute. */
export interface RegenerateGraphResult {
  reconnected: number;
  remaining_dangling: number;
  nodes: number;
  edges: number;
  data_version: number;
}

/**
 * Regenerate the graph (POST /graph/recompute): reconnect cross-ingest wikilinks AND force a
 * fresh server-side ForceAtlas2 recompute — even when data_version has not changed.
 *
 * This is what the "Regenerate graph" button calls. Unlike reresolveLinks() (which only
 * recomputes when links actually changed), this ALWAYS re-runs the layout, so a layout change
 * (e.g. the outlier clamp that stops the graph collapsing to a dot) takes effect on demand.
 *
 * INVARIANT I2: layout is computed server-side; the client only asks for the recompute and
 * then refetches the precomputed coords via fetchGraph().
 *
 * @param signal - Optional AbortSignal for cancellation.
 */
export async function recomputeGraph(signal?: AbortSignal): Promise<RegenerateGraphResult> {
  const url = `${apiBase()}/graph/recompute`;
  const res = await apiFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    ...(signal !== undefined ? { signal } : {}),
  });
  await checkResponse(res);
  return (await res.json()) as RegenerateGraphResult;
}

// ─── R9-5: Community panel + edge breakdown ───────────────────────────────────

/** One member in the community detail response (R9-5). */
export interface CommunityMember {
  id: string;
  title: string;
  page_type: string | null;
  degree: number;
}

/** Response from GET /graph/communities/{id} (R9-5). */
export interface CommunityDetail {
  community_id: number;
  size: number;
  cohesion: number;
  cohesion_warning: boolean;
  members: CommunityMember[];
}

/**
 * Fetch community detail for the community drill-down panel (R9-5).
 * GET /graph/communities/{id}
 *
 * Returns 409 when the graph cache is cold (layout not yet computed).
 * Callers should handle ApiError with status 409 by showing a toast.
 *
 * INVARIANT I2: read-only; no layout computed client-side.
 */
export async function fetchCommunityDetail(
  communityId: number,
  signal?: AbortSignal,
): Promise<CommunityDetail> {
  const url = `${apiBase()}/graph/communities/${encodeURIComponent(communityId)}`;
  const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as CommunityDetail;
}

/** 4-signal edge weight breakdown. */
export interface EdgeBreakdown {
  direct_links: number;
  shared_sources: number;
  adamic_adar: number;
  type_affinity: number;
}

/** Response from GET /graph/edges/{src}/{tgt} (R9-5). */
export interface EdgeDetail {
  weight: number;
  breakdown: EdgeBreakdown;
  /** ISO-8601 timestamp when the edge weight was last computed; null if not yet persisted. */
  computed_at?: string | null;
}

/**
 * Fetch edge weight breakdown for the edge tooltip (R9-5).
 * GET /graph/edges/{src}/{tgt}
 *
 * Returns 404 when the edge does not exist.
 * Fetched on-demand (hover with 150 ms debounce) and cached per pair in
 * GraphViewer component state. INVARIANT I2: read-only; no layout computed.
 */
export async function fetchEdgeDetail(
  srcId: string,
  tgtId: string,
  signal?: AbortSignal,
): Promise<EdgeDetail> {
  const url = `${apiBase()}/graph/edges/${encodeURIComponent(srcId)}/${encodeURIComponent(tgtId)}`;
  const res = await apiFetch(url, signal !== undefined ? { signal } : undefined);
  await checkResponse(res);
  return (await res.json()) as EdgeDetail;
}
