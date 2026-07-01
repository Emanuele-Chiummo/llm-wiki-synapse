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
  /** Display label */
  label: string;
  /** Page type for color-coding in the legend */
  type: string | null;
  /**
   * Visual radius in pixels (normalized sqrt over degree range).
   * MIN_R=4, MAX_R=22. t = (sqrt(d)-sqrt(dMin))/(sqrt(dMax)-sqrt(dMin));
   * radius = MIN_R + t*(MAX_R-MIN_R). If dMax==dMin use (MIN_R+MAX_R)/2.
   * Recomputed over the full node set — never per-frame.
   */
  size: number;
  /** Structural degree (for reducers & size) */
  degree: number;
}

export interface EdgeAttributes {
  /** Additive weight (3·direct + 4·source + 1.5·AA + 1·type), raw from server */
  weight: number;
  /** Normalized 0..1 weight (computed at build time over all edges) */
  normalizedWeight: number;
  /** Visual thickness: 0.25 + normalizedWeight * 1.0 (faint at rest; edgeReducer brightens on hover) */
  size: number;
  /** RGBA color encoding weight + kind */
  color: string;
  /** Edge kind from server (default "link") */
  kind: "link" | "source";
}

export type SynapseGraph = Graph<NodeAttributes, EdgeAttributes>;

// ─── Size constants ───────────────────────────────────────────────────────────

const MIN_R = 4;
const MAX_R = 22;
const MID_R = (MIN_R + MAX_R) / 2;

/**
 * Compute per-node visual radii using normalized sqrt over the degree range.
 * Processes the whole node set at once so dMin/dMax are computed once only.
 * Returns a Map<nodeId, radius>.
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

  for (const n of nodes) {
    const d = n.degree ?? 0;
    let radius: number;
    if (sqrtRange === 0) {
      radius = MID_R;
    } else {
      const t = (Math.sqrt(d) - sqrtMin) / sqrtRange;
      radius = MIN_R + t * (MAX_R - MIN_R);
    }
    radii.set(n.id, radius);
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
 * Build resting-state edge color from normalized weight and kind.
 * Light theme: edges are light gray at rest — they darken on hover via edgeReducer.
 *
 * IMPORTANT: sigma v3's default edge program renders the edge RGB at full opacity and
 * effectively IGNORES the alpha channel. On a white (#ffffff) canvas we bake faintness
 * into the RGB directly: light gray at low weight ramping to a medium gray at high weight.
 * kind="source" gets a subtle blue-gray tint vs the neutral link gray.
 *
 * Resting range (light theme):
 *   low weight  → #dde0e4  (very light near --syn-border)
 *   high weight → #a8b0ba  (medium gray — still clearly visible on white)
 */
function edgeColor(normalizedWeight: number, kind: "link" | "source"): string {
  const t = Math.max(0, Math.min(1, normalizedWeight));
  if (kind === "source") {
    // Blue-gray tint: low=#d8dff0, high=#9aaac8
    const r = Math.round(216 - 30 * t);
    const g = Math.round(223 - 23 * t);
    const b = Math.round(240 - 32 * t);
    return `rgb(${r},${g},${b})`;
  }
  // Neutral gray: low=#dde0e4, high=#a8b0ba
  const r = Math.round(221 - 37 * t);
  const g = Math.round(224 - 36 * t);
  const b = Math.round(228 - 34 * t);
  return `rgb(${r},${g},${b})`;
}

// ─── Transform ───────────────────────────────────────────────────────────────

/**
 * Build a graphology Graph from the GET /graph API response.
 *
 * CRITICAL (I2): node x/y are taken verbatim from `nodes[i].x` / `nodes[i].y`.
 * No layout algorithm is called. No rAF loop is started. Positions are fixed.
 *
 * Node sizes: normalized-sqrt over degree range (MIN_R=4, MAX_R=22).
 * Edge sizes/colors: normalized weight [0..1] applied at build time.
 *
 * @param nodes - Node array from GraphResponse (must include x, y)
 * @param edges - Edge array from GraphResponse
 * @returns A graphology UndirectedGraph ready for sigma to render
 */
export function buildGraphologyGraph(nodes: GraphNode[], edges: GraphEdge[]): SynapseGraph {
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
  const radii = computeNodeRadii(nodes);

  // Compute normalized weights over the full edge set (done once at build time)
  const normalizedWeights = computeNormalizedWeights(edges);

  // Add all nodes with SERVER-PROVIDED coords — I2: do NOT call any layout here
  for (const node of nodes) {
    graph.addNode(node.id, {
      x: node.x, // precomputed by server FA2 — DO NOT RECOMPUTE
      y: node.y, // precomputed by server FA2 — DO NOT RECOMPUTE
      label: node.title,
      type: node.type,
      size: radii.get(node.id) ?? MID_R,
      degree: node.degree ?? 0,
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
      size: 0.5 + nw * 1.5, // slightly thicker for better visibility on light background
      color: edgeColor(nw, kind),
      kind,
    });
  }

  return graph;
}
