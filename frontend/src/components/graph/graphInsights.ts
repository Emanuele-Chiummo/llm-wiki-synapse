/**
 * graphInsights.ts — pure, testable logic for Graph Insights panel (F4, G-P1-5).
 *
 * INVARIANT I2: this module NEVER computes layout or community membership. It
 *   only reads community/cohesion values that the server already returned.
 *   Derived analytics (surprising connections, gaps, bridges) operate on
 *   server-supplied node.community and edge.weight fields.
 *
 * INVARIANT I3: all loops are bounded by nodes.length or edges.length;
 *   no nested O(N²) beyond the adjacency-map build (O(E));
 *   adjacency is built once and reused across all gap sub-kinds.
 *
 * Mirrors llm_wiki client-side graph-insights.ts behavior:
 *   - Surprising connections: cross-community edges with weight >= 3, top 8.
 *   - Knowledge gaps:
 *       isolated   — degree <= 1
 *       sparse     — community cohesion < 0.15 AND size >= 3
 *       bridge     — node with >= 3 distinct neighbor communities (excl. own + unassigned)
 *
 * All returned items carry a STABLE `id` string and a `primaryNodeId` for
 * click-to-highlight in the panel.
 */

import type { GraphNode, GraphEdge, GraphCommunity } from "../../api/types";

// ─── Constants ────────────────────────────────────────────────────────────────

/** Edge weight threshold for "surprising connection" detection. */
const SURPRISING_WEIGHT_THRESHOLD = 3;

/** Maximum surprising-connection items returned (sorted by score desc). */
const SURPRISING_MAX = 8;

/** Cohesion threshold below which a community is "sparse" (roadmap spec). */
const SPARSE_COHESION_THRESHOLD = 0.15;

/** Minimum community size to be considered "sparse" (avoids trivial singletons). */
const SPARSE_MIN_SIZE = 3;

/** Minimum distinct neighbor-community count to be flagged as a "bridge" node. */
const BRIDGE_MIN_COMMUNITIES = 3;

// ─── Meta-node filter ─────────────────────────────────────────────────────────

/** Types that are considered meta-infrastructure nodes (excluded from insights). */
const META_TYPES = new Set(["index", "log"]);

/** Titles (lowercase + trimmed) considered meta-infrastructure. */
const META_TITLES = new Set(["index", "log", "overview"]);

/**
 * Returns true for meta-infrastructure nodes that should be excluded
 * from all insight computation (index.md, log.md, overview.md).
 */
export function isMetaNode(node: GraphNode): boolean {
  if (node.type !== null && META_TYPES.has(node.type.toLowerCase())) return true;
  const titleKey = node.title.toLowerCase().trim();
  return META_TITLES.has(titleKey);
}

// ─── Insight types ────────────────────────────────────────────────────────────

/** The kind of insight item. */
export type InsightKind = "surprising" | "gap-isolated" | "gap-sparse" | "gap-bridge";

/** Base fields shared by all insight items. */
interface InsightBase {
  /** Stable id for tracking dismissals. */
  id: string;
  kind: InsightKind;
  /**
   * Node id to pass to setSelectedNodeId for click-to-highlight.
   * null only for gap-sparse when no member node can be found.
   */
  primaryNodeId: string | null;
  /** Topic string to seed deep-research (node title or community label). */
  topic: string;
}

/** A cross-community high-weight edge (surprising connection). */
export interface SurprisingInsight extends InsightBase {
  kind: "surprising";
  sourceNodeId: string;
  sourceTitle: string;
  targetNodeId: string;
  targetTitle: string;
  score: number;
  sourceCommunity: number;
  targetCommunity: number;
}

/** A node with degree <= 1 (isolated from the rest of the graph). */
export interface GapIsolatedInsight extends InsightBase {
  kind: "gap-isolated";
  nodeId: string;
  nodeTitle: string;
}

/** A community with cohesion < threshold and size >= minimum. */
export interface GapSparseInsight extends InsightBase {
  kind: "gap-sparse";
  communityId: number;
  cohesion: number;
  size: number;
}

/** A node whose neighbors span >= 3 distinct communities. */
export interface GapBridgeInsight extends InsightBase {
  kind: "gap-bridge";
  nodeId: string;
  nodeTitle: string;
  neighborCommunityCount: number;
}

/** Union type of all insight items. */
export type InsightItem =
  SurprisingInsight | GapIsolatedInsight | GapSparseInsight | GapBridgeInsight;

// ─── Output shape ─────────────────────────────────────────────────────────────

/** Grouped result returned by computeGraphInsights. */
export interface GraphInsights {
  surprising: SurprisingInsight[];
  gapIsolated: GapIsolatedInsight[];
  gapSparse: GapSparseInsight[];
  gapBridge: GapBridgeInsight[];
  /** Total count across all groups. */
  total: number;
}

// ─── Main computation ─────────────────────────────────────────────────────────

/**
 * Compute all graph insights from the current graph data.
 *
 * Performance contract (I3):
 *   - One pass over edges to build the adjacency map (O(E)).
 *   - One pass over edges to find cross-community candidates (O(E)).
 *   - One pass over nodes to find isolated + bridge (O(N + E) via adjacency).
 *   - One pass over communities for sparse detection (O(C)).
 *   - No nested O(N²) loops.
 *
 * I2: community and cohesion values are READ from server-supplied data only.
 *     This function never runs Louvain or any layout algorithm.
 */
export function computeGraphInsights(
  nodes: GraphNode[],
  edges: GraphEdge[],
  communities: GraphCommunity[],
): GraphInsights {
  // ── Build node index (O(N)) ────────────────────────────────────────────────
  const nodeById = new Map<string, GraphNode>();
  for (const node of nodes) {
    nodeById.set(node.id, node);
  }

  // ── Build adjacency map: nodeId → Set<neighborId> (O(E)) ──────────────────
  const adjacency = new Map<string, Set<string>>();
  for (const edge of edges) {
    if (!adjacency.has(edge.source)) adjacency.set(edge.source, new Set());
    if (!adjacency.has(edge.target)) adjacency.set(edge.target, new Set());
    // Sets are guaranteed to exist — we just ensured they do above.
    // Use a local reference to avoid the non-null assertion lint warning.
    const srcSet = adjacency.get(edge.source);
    const tgtSet = adjacency.get(edge.target);
    if (srcSet !== undefined) srcSet.add(edge.target);
    if (tgtSet !== undefined) tgtSet.add(edge.source);
  }

  // ── 1. Surprising connections (cross-community, weight >= threshold) ───────
  const surprising: SurprisingInsight[] = [];

  for (const edge of edges) {
    const srcNode = nodeById.get(edge.source);
    const tgtNode = nodeById.get(edge.target);
    if (srcNode === undefined || tgtNode === undefined) continue;
    if (isMetaNode(srcNode) || isMetaNode(tgtNode)) continue;

    const srcCommunity = srcNode.community ?? -1;
    const tgtCommunity = tgtNode.community ?? -1;

    // Both must be assigned to a community (not -1) and be in different communities
    if (srcCommunity < 0 || tgtCommunity < 0) continue;
    if (srcCommunity === tgtCommunity) continue;

    if (edge.weight < SURPRISING_WEIGHT_THRESHOLD) continue;

    surprising.push({
      kind: "surprising",
      id: `surprising:${edge.source}:${edge.target}`,
      primaryNodeId: edge.source,
      topic: `${srcNode.title} — ${tgtNode.title}`,
      sourceNodeId: edge.source,
      sourceTitle: srcNode.title,
      targetNodeId: edge.target,
      targetTitle: tgtNode.title,
      score: edge.weight,
      sourceCommunity: srcCommunity,
      targetCommunity: tgtCommunity,
    });
  }

  // Sort by score descending, cap at SURPRISING_MAX
  surprising.sort((a, b) => b.score - a.score);
  const topSurprising = surprising.slice(0, SURPRISING_MAX);

  // ── 2. Gap: isolated nodes (degree <= 1) ────────────────────────────────────
  const gapIsolated: GapIsolatedInsight[] = [];

  for (const node of nodes) {
    if (isMetaNode(node)) continue;
    const degree = node.degree ?? 0;
    if (degree <= 1) {
      gapIsolated.push({
        kind: "gap-isolated",
        id: `gap-isolated:${node.id}`,
        primaryNodeId: node.id,
        topic: node.title,
        nodeId: node.id,
        nodeTitle: node.title,
      });
    }
  }

  // ── 3. Gap: sparse communities (cohesion < threshold AND size >= min) ───────
  const gapSparse: GapSparseInsight[] = [];

  // Build community → first-member node map for primaryNodeId (O(N))
  const communityFirstNode = new Map<number, string>();
  for (const node of nodes) {
    if (isMetaNode(node)) continue;
    const c = node.community ?? -1;
    if (c >= 0 && !communityFirstNode.has(c)) {
      communityFirstNode.set(c, node.id);
    }
  }

  for (const community of communities) {
    if (community.cohesion < SPARSE_COHESION_THRESHOLD && community.size >= SPARSE_MIN_SIZE) {
      const primaryNodeId = communityFirstNode.get(community.id) ?? null;
      gapSparse.push({
        kind: "gap-sparse",
        id: `gap-sparse:${community.id}`,
        primaryNodeId,
        topic: `Community ${community.id}`,
        communityId: community.id,
        cohesion: community.cohesion,
        size: community.size,
      });
    }
  }

  // ── 4. Gap: bridge nodes (>= 3 distinct neighbor communities) ────────────────
  const gapBridge: GapBridgeInsight[] = [];

  for (const node of nodes) {
    if (isMetaNode(node)) continue;
    const ownCommunity = node.community ?? -1;
    const neighbors = adjacency.get(node.id);
    if (neighbors === undefined || neighbors.size === 0) continue;

    const neighborCommunities = new Set<number>();
    for (const neighborId of neighbors) {
      const neighbor = nodeById.get(neighborId);
      if (neighbor === undefined) continue;
      const nc = neighbor.community ?? -1;
      // Exclude unassigned (-1) and the node's own community
      if (nc < 0 || nc === ownCommunity) continue;
      neighborCommunities.add(nc);
    }

    if (neighborCommunities.size >= BRIDGE_MIN_COMMUNITIES) {
      gapBridge.push({
        kind: "gap-bridge",
        id: `gap-bridge:${node.id}`,
        primaryNodeId: node.id,
        topic: node.title,
        nodeId: node.id,
        nodeTitle: node.title,
        neighborCommunityCount: neighborCommunities.size,
      });
    }
  }

  const total = topSurprising.length + gapIsolated.length + gapSparse.length + gapBridge.length;

  return {
    surprising: topSurprising,
    gapIsolated,
    gapSparse,
    gapBridge,
    total,
  };
}
