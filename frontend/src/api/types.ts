/**
 * API contract types for Synapse frontend.
 *
 * Mirrors the GET /graph response shape defined in:
 *   docs/sprints/v0.3-architecture.md §6
 *   docs/api/openapi.json
 *
 * INVARIANT I2: coords (x, y) come FROM the server; the client NEVER computes layout.
 */

// ─── GET /graph ─────────────────────────────────────────────────────────────

export interface GraphNode {
  /** UUID string — matches pages.id in Postgres */
  id: string;
  /** Page title */
  title: string;
  /** Page type (concept | entity | source | etc.) — may be null */
  type: string | null;
  /** FA2 x-coordinate — server-precomputed, stored in pages.x (I2) */
  x: number;
  /** FA2 y-coordinate — server-precomputed, stored in pages.y (I2) */
  y: number;
  /** Rendering hint: monotonic in degree, default 1.0 — derived, not persisted */
  size?: number;
  /** Incident-edge count — derived, not persisted */
  degree?: number;
}

export interface GraphEdge {
  /** Source page UUID */
  source: string;
  /** Target page UUID */
  target: string;
  /** Additive weight (I2 formula: 3·direct + 4·source_overlap + 1.5·AA + 1·same_type) */
  weight: number;
  /**
   * Edge kind (v0.4 contract).
   * "link"   = wikilink edge (direct structural reference)
   * "source" = shared-source-document overlap edge
   * Omitted / undefined = treat as "link" (back-compat with v0.3 server).
   */
  kind?: "link" | "source";
}

export interface GraphResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
  /** Data version the coords correspond to */
  data_version: number;
  /** true = X-Graph-Cache: hit (no FA2 ran); false = miss (inline recompute) */
  cached: boolean;
}

/** Value of the X-Graph-Cache response header */
export type CacheStatus = "hit" | "miss" | "unknown";

// ─── GET /pages/{id} ─────────────────────────────────────────────────────────

export interface PageDetail {
  id: string;
  title: string;
  type: string | null;
  vault_id: string;
}
