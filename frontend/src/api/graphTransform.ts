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
   * Louvain community id (server-computed, v0.6+).
   * -1 = unassigned/isolated. Default -1 when absent on older servers.
   * INVARIANT I2: passed through verbatim; no client community computation.
   */
  community: number;
  /**
   * Visual radius in pixels (normalized sqrt over degree range).
   * MIN_R=2.5, MAX_R=11. t = (sqrt(d)-sqrt(dMin))/(sqrt(dMax)-sqrt(dMin));
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
 * Light theme: edges are light at rest and darken on hover via edgeReducer.
 *
 * IMPORTANT: sigma v3's default edge program renders the edge RGB at full opacity and
 * effectively IGNORES the alpha channel. On a white (#ffffff) canvas we bake weight
 * into the RGB directly: light color at low weight ramping to a darker, more saturated
 * color at high weight. "Thicker + darker = stronger edge" (llm_wiki parity).
 *
 * Resting ramps (light theme):
 *   kind="link"   low=#dde0e4  → high=#7c8598  (slate gray, clearly visible on white)
 *   kind="source" low=#d8dff0  → high=#7d90bf  (slate blue-gray)
 *
 * Two-kind distinction is preserved: link=neutral gray, source=blue-gray tint.
 */
function edgeColor(normalizedWeight: number, kind: "link" | "source"): string {
  const t = Math.max(0, Math.min(1, normalizedWeight));
  if (kind === "source") {
    // Blue-gray tint: low=#d8dff0 (r=216,g=223,b=240) → high=#7d90bf (r=125,g=144,b=191)
    const r = Math.round(216 - 91 * t);
    const g = Math.round(223 - 79 * t);
    const b = Math.round(240 - 49 * t);
    return `rgb(${r},${g},${b})`;
  }
  // Neutral slate gray: low=#dde0e4 (r=221,g=224,b=228) → high=#7c8598 (r=124,g=133,b=152)
  const r = Math.round(221 - 97 * t);
  const g = Math.round(224 - 91 * t);
  const b = Math.round(228 - 76 * t);
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
      // community passed verbatim; -1 when absent (I2: no client computation)
      community: node.community ?? -1,
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
      size: EDGE_MIN_SIZE + nw * EDGE_SIZE_RANGE, // 0.5–4 px: thicker + darker = stronger (llm_wiki parity)
      color: edgeColor(nw, kind),
      kind,
    });
  }

  return graph;
}
