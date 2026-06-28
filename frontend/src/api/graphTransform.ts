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
  /** Node size proportional to degree */
  size: number;
}

export interface EdgeAttributes {
  /** Additive weight (3·direct + 4·source + 1.5·AA + 1·type) */
  weight: number;
}

export type SynapseGraph = Graph<NodeAttributes, EdgeAttributes>;

// ─── Default sizes ────────────────────────────────────────────────────────────

const DEFAULT_SIZE = 5;
const SIZE_SCALE = 2;

/**
 * Compute a visual node size from degree.
 * Monotonically increasing with degree, minimum DEFAULT_SIZE.
 */
function nodeSize(degree: number | undefined, serverSize: number | undefined): number {
  if (serverSize !== undefined && serverSize > 0) {
    return serverSize * DEFAULT_SIZE;
  }
  return DEFAULT_SIZE + (degree ?? 0) * SIZE_SCALE;
}

// ─── Transform ───────────────────────────────────────────────────────────────

/**
 * Build a graphology Graph from the GET /graph API response.
 *
 * CRITICAL (I2): node x/y are taken verbatim from `nodes[i].x` / `nodes[i].y`.
 * No layout algorithm is called. No rAF loop is started. Positions are fixed.
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

  // Add all nodes with SERVER-PROVIDED coords — I2: do NOT call any layout here
  for (const node of nodes) {
    graph.addNode(node.id, {
      x: node.x, // precomputed by server FA2 — DO NOT RECOMPUTE
      y: node.y, // precomputed by server FA2 — DO NOT RECOMPUTE
      label: node.title,
      type: node.type,
      size: nodeSize(node.degree, node.size),
    });
  }

  // Add edges — skip if either endpoint is not in the graph (defensive)
  for (const edge of edges) {
    if (graph.hasNode(edge.source) && graph.hasNode(edge.target)) {
      // graphology will throw on duplicate edges in non-multi mode; skip duplicates
      if (!graph.hasEdge(edge.source, edge.target)) {
        graph.addEdge(edge.source, edge.target, { weight: edge.weight });
      }
    }
  }

  return graph;
}
