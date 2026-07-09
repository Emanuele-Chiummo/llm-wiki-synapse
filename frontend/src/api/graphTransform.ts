/**
 * graphTransform.ts — Pure function: GraphResponse → graphology Graph.
 *
 * INVARIANT I2 (ADR-0015 §1 HARD RULE):
 *   This function sets each node's x/y DIRECTLY from the server response.
 *   It MUST NOT call any layout function. It MUST NOT import:
 *     - graphology-layout-forceatlas2
 *     - graphology-layout (any variant)
 *     - d3-force / forceSimulation
 *     - @antv/layout
 *     - any rAF loop that mutates node positions
 *
 * This is a pure function so it can be unit-tested in isolation (AC-FE-5, ADR-0015 §3).
 * The test (graph-transform.test.ts) asserts that output node coords match input coords exactly.
 */

import Graph from "graphology";
import type { GraphNode, GraphEdge } from "./types";

// ─── Graphology attribute types ───────────────────────────────────────────────

export interface NodeAttributes {
  /** Server-precomputed FA2 x — I2: never mutated by client layout */
  x: number;
  /** Server-precomputed FA2 y — I2: never mutated by client layout */
  y: number;
  /** Display label (full title) */
  label: string;
  /**
   * Truncated hub label (≤18 chars + "…").
   * Only meaningful when forceLabel=true. Full title stays in label/tooltip.
   * Stored at build time so no string work happens per-frame (I3).
   */
  hubLabel: string;
  /** Page type for color-coding in the legend */
  type: string | null;
  /**
   * Louvain community id (server-computed, v0.6+).
   * -1 = unassigned/isolated. Default -1 when absent on older servers.
   * INVARIANT I2: passed through verbatim; no client community computation.
   */
  community: number;
  /**
   * Visual radius in pixels (normalized sqrt over degree range).
   * MIN_R=2.5, MAX_R=11. t = (sqrt(d)-sqrt(dMin))/(sqrt(dMax)-sqrt(dMin));
   * radius = MIN_R + t*(MAX_R-MIN_R). If dMax==dMin use (MIN_R+MAX_R)/2.
   * GL3: further scaled by densityScale(n) with a 2px floor.
   * Recomputed over the full node set — never per-frame.
   */
  size: number;
  /** Structural degree (for reducers & size) */
  degree: number;
  /**
   * GL2: true for top-K hub nodes (K = min(6, ceil(n*0.01))).
   * nodeReducer uses this to force a permanent truncated label on the busiest hubs.
   * Computed once at build time from the degree ranking — not per-frame.
   */
  forceLabel: boolean;
}

export interface EdgeAttributes {
  /** Additive weight (3·direct + 4·source + 1.5·AA + 1·type), raw from server */
  weight: number;
  /** Normalized 0..1 weight (computed at build time over all edges) */
  normalizedWeight: number;
  /**
   * Visual thickness: EDGE_MIN_SIZE + normalizedWeight * EDGE_SIZE_RANGE (0.5–4 px).
   * Matches llm_wiki: strong (high-normW) edges read clearly on white via thicker + darker.
   * edgeReducer further scales incident edges on hover.
   */
  size: number;
  /** RGB color encoding weight + kind (alpha channel baked in — sigma v3 ignores alpha) */
  color: string;
  /** Edge kind from server (default "link") */
  kind: "link" | "source";
  /**
   * GL1: resting-state hidden flag.
   * True when normalizedWeight < edgeVisibilityThreshold(nodeCount) at rest.
   * Edges are NEVER removed from the graph — they are hidden via sigma's hidden attribute
   * so the edgeReducer can reveal them on hover. I2-safe: render-only, no layout change.
   */
  hidden: boolean;
}

export type SynapseGraph = Graph<NodeAttributes, EdgeAttributes>;

// ─── Size constants ───────────────────────────────────────────────────────────
// Node radius scales with sqrt(structural degree): more links → bigger node.
// Retuned smaller (was 4–22) so a dense graph reads clearly without overlapping blobs;
// the degree→size relationship is unchanged, only the px range is tighter.
const MIN_R = 2.5;
const MAX_R = 11;
const MID_R = (MIN_R + MAX_R) / 2;

// ─── Edge size constants (llm_wiki parity) ───────────────────────────────────
// Range 0.5–4 px: "thicker + darker = stronger edge" visual cue on white canvas.
// edgeReducer scales incident edges further on hover (×2).
const EDGE_MIN_SIZE = 0.5;
const EDGE_SIZE_RANGE = 3.5; // → max edge size = 4.0 px

// ─── GL1: Edge visibility threshold (B3-LOOK) ────────────────────────────────
// At rest, only edges with normalizedWeight ≥ threshold are shown.
// This culls weak edges on large graphs so cluster structure becomes legible,
// matching the visual density of nashsu/llm_wiki's graph output.
// Edges below threshold are HIDDEN (sigma hidden:true) — never removed from the
// graph so the edgeReducer can reveal them on hover (I2-safe: render only).
//
// Buckets — 1:1 with nashsu/llm_wiki 0.6.0 (graph-view.tsx edgeVisibilityThreshold):
//   n ≤ 700          → 0.00  (show all)
//   700 < n ≤ 1200   → 0.05
//   1200 < n ≤ 2500  → 0.10
//   n > 2500         → 0.16
export function edgeVisibilityThreshold(nodeCount: number): number {
  if (nodeCount <= 700) return 0;
  if (nodeCount <= 1200) return 0.05;
  if (nodeCount <= 2500) return 0.1;
  return 0.16;
}

// ─── GL2: Hub label truncation ────────────────────────────────────────────────
// Hub nodes always show a label at rest (forceLabel:true). To prevent long
// titles (e.g. "Software Asset Management (SAM) — ServiceNow ITAM Overview")
// from overlapping in the dense center, we truncate to HUB_LABEL_MAX chars.
// Full title is preserved in `label` and shown in the tooltip on click.
const HUB_LABEL_MAX = 18;

export function truncateHubLabel(title: string): string {
  if (title.length <= HUB_LABEL_MAX) return title;
  return title.slice(0, HUB_LABEL_MAX) + "…";
}

// ─── GL2: Top-K hub selection (B3-LOOK) ──────────────────────────────────────
// Returns the Set of node IDs that are "hubs" — the top-K by degree.
// K = min(6, ceil(n * 0.01)) — reduced from min(10, ceil(n*0.02)) to cut
// label clutter in the dense center of large graphs (declutter pass 2026-07).
// These nodes get forceLabel:true so the busiest hubs always show their
// truncated title at rest, like nashsu/llm_wiki's labeled map.
// Computed once at graph build time — NOT per frame (I3).
export function computeTopKHubs(nodes: GraphNode[]): Set<string> {
  const n = nodes.length;
  if (n === 0) return new Set();
  const k = Math.min(6, Math.ceil(n * 0.01));
  // Sort descending by degree; take top-k ids
  const sorted = nodes
    .slice()
    .sort((a, b) => (b.degree ?? 0) - (a.degree ?? 0))
    .slice(0, k);
  return new Set(sorted.map((node) => node.id));
}

// ─── GL3: Density down-scale factor (B3-LOOK) ────────────────────────────────
// Multiplied into the computed node radius AFTER the MIN_R..MAX_R normalization.
// At ~986 nodes this ≈ 0.39, shrinking nodes to reduce overlap (llm_wiki parity).
// Clamped [0.4, 1.0] so the factor never eliminates nodes on very large graphs,
// and never inflates nodes on small ones. A hard floor of 2px is applied after.
// I2-safe: pure render scaling, no position change.
export function densityScale(nodeCount: number): number {
  if (nodeCount <= 0) return 1.0;
  return Math.min(1.0, Math.max(0.4, Math.sqrt(150 / nodeCount)));
}

// GL3: hard floor for node radius after density scaling (ensures visibility).
const NODE_SIZE_FLOOR = 2.0;

/**
 * Compute per-node visual radii using normalized sqrt over the degree range.
 * Processes the whole node set at once so dMin/dMax are computed once only.
 * Returns a Map<nodeId, radius>.
 *
 * GL3 (B3-LOOK): applies densityScale(n) after MIN_R..MAX_R normalization,
 * then clamps to NODE_SIZE_FLOOR so the smallest node stays visible.
 *
 * INVARIANT I2: does NOT read or modify x/y — pure sizing, no layout.
 */
function computeNodeRadii(nodes: GraphNode[]): Map<string, number> {
  const radii = new Map<string, number>();

  if (nodes.length === 0) return radii;

  // Use server-provided degree; fall back to 0
  let dMin = Infinity;
  let dMax = -Infinity;

  for (const n of nodes) {
    const d = n.degree ?? 0;
    if (d < dMin) dMin = d;
    if (d > dMax) dMax = d;
  }

  const sqrtMin = Math.sqrt(dMin);
  const sqrtMax = Math.sqrt(dMax);
  const sqrtRange = sqrtMax - sqrtMin;

  // GL3: compute the density scale factor once for the full node set
  const scale = densityScale(nodes.length);

  for (const n of nodes) {
    const d = n.degree ?? 0;
    let radius: number;
    if (sqrtRange === 0) {
      radius = MID_R;
    } else {
      const t = (Math.sqrt(d) - sqrtMin) / sqrtRange;
      radius = MIN_R + t * (MAX_R - MIN_R);
    }
    // GL3: apply density down-scale, then enforce hard floor
    radii.set(n.id, Math.max(NODE_SIZE_FLOOR, radius * scale));
  }

  return radii;
}

/**
 * Compute normalized weight [0..1] for each edge across the whole edge set.
 * Returns a Map<edgeIndex, normalizedWeight> (indexed by position in array).
 */
function computeNormalizedWeights(edges: GraphEdge[]): Float64Array {
  const result = new Float64Array(edges.length);
  if (edges.length === 0) return result;

  let wMin = Infinity;
  let wMax = -Infinity;

  for (const e of edges) {
    if (e.weight < wMin) wMin = e.weight;
    if (e.weight > wMax) wMax = e.weight;
  }

  const range = wMax - wMin;

  for (let i = 0; i < edges.length; i++) {
    const e = edges[i];
    if (e === undefined) continue;
    result[i] = range === 0 ? 0.5 : (e.weight - wMin) / range;
  }

  return result;
}

/**
 * Build resting-state edge color from normalized weight — NEUTRAL SLATE, llm_wiki 0.6.0
 * parity (the reference draws every edge in slate-500 with a weight→opacity ramp and makes
 * no link/source colour distinction). sigma v3's edge program ignores the alpha channel, so
 * we bake the reference's opacity into the RGB: dim near the canvas background at low weight,
 * ramping to slate-500 (#64748b) at high weight. "Thicker + brighter = stronger edge." The
 * hover highlight (cyan) is applied separately in the edgeReducer.
 *
 * Resting ramps:
 *   dark  low=#1b212a (near #0d1117 bg) → high=#64748b (slate-500)
 *   light low=#dfe3e9 (near white)      → high=#64748b (slate-500)
 */
function edgeColor(normalizedWeight: number, theme: "light" | "dark" = "light"): string {
  const t = Math.max(0, Math.min(1, normalizedWeight));
  if (theme === "dark") {
    const r = Math.round(27 + 73 * t);
    const g = Math.round(33 + 83 * t);
    const b = Math.round(42 + 97 * t);
    return `rgb(${r},${g},${b})`;
  }
  const r = Math.round(223 - 123 * t);
  const g = Math.round(227 - 111 * t);
  const b = Math.round(233 - 94 * t);
  return `rgb(${r},${g},${b})`;
}

// ─── Transform ───────────────────────────────────────────────────────────────

/**
 * Build a graphology Graph from the GET /graph API response.
 *
 * CRITICAL (I2): node x/y are taken verbatim from `nodes[i].x` / `nodes[i].y`.
 * No layout algorithm is called. No rAF loop is started. Positions are fixed.
 *
 * Node sizes: normalized-sqrt over degree range (MIN_R=2.5, MAX_R=11),
 *   then scaled by GL3 densityScale(n) with a 2px floor.
 * Edge sizes/colors: normalized weight [0..1] applied at build time.
 * GL1: edges below edgeVisibilityThreshold(n) are hidden at rest (hidden:true),
 *   but never removed — edgeReducer reveals them on hover.
 * GL2: top-K hub nodes (by degree) get forceLabel:true so they always show
 *   their truncated label (hubLabel) at rest. K = min(6, ceil(n*0.01)).
 *
 * @param nodes - Node array from GraphResponse (must include x, y)
 * @param edges - Edge array from GraphResponse
 * @returns A graphology UndirectedGraph ready for sigma to render
 */
export function buildGraphologyGraph(
  nodes: GraphNode[],
  edges: GraphEdge[],
  theme: "light" | "dark" = "light",
): SynapseGraph {
  // I2 DEV ASSERTION: verify no layout was applied (coords should be non-zero after a real ingest)
  if (typeof __DEV__ !== "undefined" && __DEV__ && nodes.length > 0) {
    const allZero = nodes.every((n) => n.x === 0 && n.y === 0);
    if (allZero && nodes.length > 1) {
      console.warn(
        "[I2/GraphTransform] All nodes have x=0,y=0 — server may not have run FA2 yet. " +
          "Rendering as-is (no client layout will be applied).",
      );
    }
  }

  const graph = new Graph<NodeAttributes, EdgeAttributes>({ multi: false, type: "undirected" });

  // Compute per-node radii over the full set (done once, not per-frame — spec §NODE SIZE)
  // GL3 densityScale is applied inside computeNodeRadii.
  const radii = computeNodeRadii(nodes);

  // Compute normalized weights over the full edge set (done once at build time)
  const normalizedWeights = computeNormalizedWeights(edges);

  // GL1: edge culling threshold — computed once for the full node count
  const edgeThreshold = edgeVisibilityThreshold(nodes.length);

  // GL2: hub node set — computed once (top-K by degree, K=min(6,ceil(n*0.01)))
  const hubNodeIds = computeTopKHubs(nodes);

  // Add all nodes with SERVER-PROVIDED coords — I2: do NOT call any layout here
  for (const node of nodes) {
    const isHub = hubNodeIds.has(node.id);
    graph.addNode(node.id, {
      x: node.x, // precomputed by server FA2 — DO NOT RECOMPUTE
      y: node.y, // precomputed by server FA2 — DO NOT RECOMPUTE
      label: node.title,
      // GL2: truncated label for hub nodes so long titles don't overlap.
      // Full title stays in `label` and is shown in tooltip/on click.
      hubLabel: isHub ? truncateHubLabel(node.title) : node.title,
      type: node.type,
      // community passed verbatim; -1 when absent (I2: no client computation)
      community: node.community ?? -1,
      size: radii.get(node.id) ?? MID_R,
      degree: node.degree ?? 0,
      // GL2: hub nodes always show truncated label (nodeReducer reads this)
      forceLabel: isHub,
    });
  }

  // Add edges — skip if either endpoint is not in the graph (defensive)
  for (let i = 0; i < edges.length; i++) {
    const edge = edges[i];
    if (edge === undefined) continue;
    if (!graph.hasNode(edge.source) || !graph.hasNode(edge.target)) continue;
    // graphology will throw on duplicate edges in non-multi mode; skip duplicates
    if (graph.hasEdge(edge.source, edge.target)) continue;

    const nw = normalizedWeights[i] ?? 0.5;
    const kind: "link" | "source" = edge.kind ?? "link";

    graph.addEdge(edge.source, edge.target, {
      weight: edge.weight,
      normalizedWeight: nw,
      size: EDGE_MIN_SIZE + nw * EDGE_SIZE_RANGE, // 0.5–4 px: thicker + darker = stronger (llm_wiki parity)
      color: edgeColor(nw, theme),
      kind,
      // GL1: hide weak edges at rest; edgeReducer reveals them on hover (I2-safe)
      hidden: nw < edgeThreshold,
    });
  }

  return graph;
}
