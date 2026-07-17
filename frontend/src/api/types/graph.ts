/**
 * Graph API contract types (FE-QUAL-11 split of api/types.ts).
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
  /**
   * Louvain community id (server-computed, v0.6+).
   * 0 = largest community; -1 = unassigned / isolated.
   * Absent on older server responses (non-breaking additive field).
   * INVARIANT I2: client NEVER recomputes community; only reads this value.
   */
  community?: number;
  /**
   * The dominant domain name for this node's page (e.g. "SAM", "Procurement").
   * Derived server-side from the page's domain/… tag in the controlled vocabulary.
   * null when the page is untagged or no domain vocabulary is configured.
   * Absent on older server responses (non-breaking additive field).
   * INVARIANT I2: client NEVER computes or modifies this value.
   */
  domain?: string | null;
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

/** Community summary entry returned in the top-level communities array (v0.6+). */
export interface GraphCommunity {
  /** Community id (matches community field on GraphNode). */
  id: number;
  /** Number of nodes in this community. */
  size: number;
  /**
   * Louvain cohesion score (0–1).
   * Communities with cohesion < 0.1 are considered low-cohesion and
   * the UI marks them with a warning indicator in the legend.
   */
  cohesion: number;
  /**
   * Display name for this community (v0.7+, backend contract feat/b3-graph-look).
   * Derived server-side as the dominant domain name, or the top-page title, or
   * "Comunità {id}" as a fallback. Absent on older server responses — UI falls
   * back to the same "Comunità {id}" string when absent or empty.
   * INVARIANT I2: client NEVER computes this; only reads what the server returns.
   */
  label?: string;
  /**
   * The dominant domain name for this community (e.g. "SAM", "Procurement").
   * null when no domain vocabulary is configured or the community has no domain tag.
   * Absent on older server responses.
   */
  dominant_domain?: string | null;
  /**
   * The top-ranked page within this community (by degree/centrality).
   * Used as a fallback label when dominant_domain is null.
   * Absent on older server responses.
   */
  top_page?: { id: string; title: string; slug: string } | null;
}

export interface GraphResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
  /** Data version the coords correspond to */
  data_version: number;
  /** true = X-Graph-Cache: hit (no FA2 ran); false = miss (inline recompute) */
  cached: boolean;
  /**
   * Community summary list (v0.6+, server-computed Louvain).
   * Absent on older server responses (non-breaking additive field).
   * INVARIANT I2: client NEVER computes communities; only reads this list.
   */
  communities?: GraphCommunity[];
  /**
   * GR1: Total live vault pages (all pages, including those not in the graph).
   * Used as denominator for the GraphHeader pages chip.
   * Absent on older server responses — falls back to nodes.length in the UI.
   */
  total_nodes?: number;
  /**
   * GR1: Total link-table rows (NOT the same as graph edges, which include source-overlap).
   * Stored in the store for potential future use, but NOT used as denominator for the
   * edge chip (which uses edges.length from the graph payload instead — see GR1 contract).
   */
  total_edges?: number;
}

/** Value of the X-Graph-Cache response header */
export type CacheStatus = "hit" | "miss" | "unknown";
