/**
 * graph-transform.test.ts
 *
 * Tests for the pure graphology transform function (AC-FE-5, ADR-0015 §3).
 *
 * KEY ASSERTION (I2): node x/y in the graphology graph MUST EXACTLY MATCH
 * the x/y values in the API response. The transform function MUST NOT call
 * any layout algorithm. These tests verify that invariant in isolation.
 */

import { describe, it, expect, vi } from "vitest";
import {
  buildGraphologyGraph,
  edgeVisibilityThreshold,
  densityScale,
  computeTopKHubs,
} from "../api/graphTransform";
import type { GraphNode, GraphEdge } from "../api/types";

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const NODES: GraphNode[] = [
  { id: "node-1", title: "Concept A", type: "concept", x: 123.45, y: -67.89, degree: 2 },
  { id: "node-2", title: "Entity B", type: "entity", x: 0.001, y: 999.0, degree: 1 },
  { id: "node-3", title: "Source C", type: "source", x: -42.0, y: 0.0, degree: 1 },
  { id: "node-4", title: "Untyped D", type: null, x: 7.7, y: 8.8, degree: 0 },
];

const EDGES: GraphEdge[] = [
  { source: "node-1", target: "node-2", weight: 11.0 },
  { source: "node-1", target: "node-3", weight: 5.0 },
];

// ─── I2: precomputed coords ───────────────────────────────────────────────────

describe("buildGraphologyGraph — I2: server coords used verbatim", () => {
  it("assigns server x/y directly to each graphology node without modification", () => {
    const graph = buildGraphologyGraph(NODES, EDGES);

    for (const node of NODES) {
      expect(graph.hasNode(node.id)).toBe(true);
      const attrs = graph.getNodeAttributes(node.id);

      // CRITICAL: these must be EXACTLY the server values — no layout rounding
      expect(attrs.x).toBe(node.x);
      expect(attrs.y).toBe(node.y);
    }
  });

  it("preserves fractional and negative coordinates exactly", () => {
    const graph = buildGraphologyGraph(NODES, EDGES);
    const attrs1 = graph.getNodeAttributes("node-1");
    expect(attrs1.x).toBe(123.45);
    expect(attrs1.y).toBe(-67.89);
  });

  it("preserves near-zero and large coordinates exactly", () => {
    const graph = buildGraphologyGraph(NODES, EDGES);
    const attrs2 = graph.getNodeAttributes("node-2");
    expect(attrs2.x).toBe(0.001);
    expect(attrs2.y).toBe(999.0);
  });

  it("preserves negative x with zero y exactly", () => {
    const graph = buildGraphologyGraph(NODES, EDGES);
    const attrs3 = graph.getNodeAttributes("node-3");
    expect(attrs3.x).toBe(-42.0);
    expect(attrs3.y).toBe(0.0);
  });
});

// ─── I2: no layout function called ───────────────────────────────────────────

describe("buildGraphologyGraph — I2: no client-side layout invoked", () => {
  it("does NOT call Math.random (random layout sentinel)", () => {
    // A random-layout or circular-layout algorithm calls Math.random.
    // If x/y were computed client-side via a random layout, this would fire.
    const randomSpy = vi.spyOn(Math, "random");

    buildGraphologyGraph(NODES, EDGES);

    expect(randomSpy).not.toHaveBeenCalled();
    randomSpy.mockRestore();
  });

  it("does NOT call requestAnimationFrame (physics loop sentinel)", () => {
    // An rAF-based physics layout would call requestAnimationFrame.
    const rafSpy = vi.spyOn(globalThis, "requestAnimationFrame").mockReturnValue(0);

    buildGraphologyGraph(NODES, EDGES);

    expect(rafSpy).not.toHaveBeenCalled();
    rafSpy.mockRestore();
  });

  it("does NOT call setTimeout (async layout sentinel)", () => {
    const timeoutSpy = vi.spyOn(globalThis, "setTimeout");

    buildGraphologyGraph(NODES, EDGES);

    expect(timeoutSpy).not.toHaveBeenCalled();
    timeoutSpy.mockRestore();
  });
});

// ─── Graph structure ──────────────────────────────────────────────────────────

describe("buildGraphologyGraph — graph structure", () => {
  it("adds all nodes from the API response", () => {
    const graph = buildGraphologyGraph(NODES, EDGES);
    expect(graph.order).toBe(NODES.length);
    for (const node of NODES) {
      expect(graph.hasNode(node.id)).toBe(true);
    }
  });

  it("sets node label from title", () => {
    const graph = buildGraphologyGraph(NODES, EDGES);
    expect(graph.getNodeAttribute("node-1", "label")).toBe("Concept A");
    expect(graph.getNodeAttribute("node-4", "label")).toBe("Untyped D");
  });

  it("sets node type attribute (null preserved)", () => {
    const graph = buildGraphologyGraph(NODES, EDGES);
    expect(graph.getNodeAttribute("node-1", "type")).toBe("concept");
    expect(graph.getNodeAttribute("node-4", "type")).toBe(null);
  });

  it("adds all valid edges", () => {
    const graph = buildGraphologyGraph(NODES, EDGES);
    expect(graph.size).toBe(EDGES.length);
    expect(graph.hasEdge("node-1", "node-2")).toBe(true);
    expect(graph.hasEdge("node-1", "node-3")).toBe(true);
  });

  it("sets edge weight attribute", () => {
    const graph = buildGraphologyGraph(NODES, EDGES);
    const edgeKey = graph.edge("node-1", "node-2");
    expect(graph.getEdgeAttribute(edgeKey, "weight")).toBe(11.0);
  });

  it("skips edges with unknown endpoints gracefully", () => {
    const badEdges: GraphEdge[] = [
      { source: "node-1", target: "ghost-99", weight: 3.0 },
    ];
    const graph = buildGraphologyGraph(NODES, badEdges);
    // ghost-99 not in nodes → edge should be silently skipped
    expect(graph.size).toBe(0);
  });

  it("handles empty nodes and edges", () => {
    const graph = buildGraphologyGraph([], []);
    expect(graph.order).toBe(0);
    expect(graph.size).toBe(0);
  });

  it("handles a single node with no edges", () => {
    const single: GraphNode[] = [{ id: "solo", title: "Solo", type: null, x: 1, y: 2 }];
    const graph = buildGraphologyGraph(single, []);
    expect(graph.order).toBe(1);
    expect(graph.size).toBe(0);
    expect(graph.getNodeAttribute("solo", "x")).toBe(1);
    expect(graph.getNodeAttribute("solo", "y")).toBe(2);
  });

  it("deduplicates duplicate edges (non-multi graph)", () => {
    const dupEdges: GraphEdge[] = [
      { source: "node-1", target: "node-2", weight: 5.0 },
      { source: "node-1", target: "node-2", weight: 3.0 }, // duplicate
    ];
    const graph = buildGraphologyGraph(NODES, dupEdges);
    // Only the first edge should be added
    expect(graph.size).toBe(1);
  });
});

// ─── AC-FE-5: pure transform, no side effects ─────────────────────────────────

describe("buildGraphologyGraph — pure function, no side effects", () => {
  it("does not mutate the input nodes array", () => {
    const snapshot = NODES.map((n) => ({ ...n }));
    buildGraphologyGraph(NODES, EDGES);
    expect(NODES).toEqual(snapshot);
  });

  it("does not mutate the input edges array", () => {
    const snapshot = EDGES.map((e) => ({ ...e }));
    buildGraphologyGraph(NODES, EDGES);
    expect(EDGES).toEqual(snapshot);
  });

  it("produces an independent graphology graph each call", () => {
    const g1 = buildGraphologyGraph(NODES, EDGES);
    const g2 = buildGraphologyGraph(NODES, EDGES);
    // Mutating g1 should not affect g2
    g1.setNodeAttribute("node-1", "x", 9999);
    expect(g2.getNodeAttribute("node-1", "x")).toBe(NODES[0]!.x);
  });
});

// ─── B3-LOOK GL1: edgeVisibilityThreshold buckets ────────────────────────────

describe("edgeVisibilityThreshold — GL1 bucket boundaries", () => {
  it("returns 0 for n=0 (empty graph — show all)", () => {
    expect(edgeVisibilityThreshold(0)).toBe(0);
  });

  it("returns 0 for n=1 (single node — show all)", () => {
    expect(edgeVisibilityThreshold(1)).toBe(0);
  });

  it("returns 0 for n=150 (boundary — show all)", () => {
    expect(edgeVisibilityThreshold(150)).toBe(0);
  });

  it("returns 0.12 for n=151 (just above small-graph boundary)", () => {
    expect(edgeVisibilityThreshold(151)).toBe(0.12);
  });

  it("returns 0.12 for n=600 (boundary — medium graph)", () => {
    expect(edgeVisibilityThreshold(600)).toBe(0.12);
  });

  it("returns 0.22 for n=601 (just above medium boundary)", () => {
    expect(edgeVisibilityThreshold(601)).toBe(0.22);
  });

  it("returns 0.22 for n=1200 (boundary — large graph)", () => {
    expect(edgeVisibilityThreshold(1200)).toBe(0.22);
  });

  it("returns 0.32 for n=1201 (just above large boundary)", () => {
    expect(edgeVisibilityThreshold(1201)).toBe(0.32);
  });

  it("returns 0.32 for n=5000 (very large graph)", () => {
    expect(edgeVisibilityThreshold(5000)).toBe(0.32);
  });
});

// ─── B3-LOOK GL1: edges below threshold get hidden:true at rest ───────────────

describe("buildGraphologyGraph — GL1 edge hidden flag", () => {
  // Build a graph with 200 nodes (threshold = 0.12) so we can test culling.
  // We only need the edges to vary — nodes are synthetic with unique ids.
  function makeNodes(count: number): GraphNode[] {
    return Array.from({ length: count }, (_, i) => ({
      id: `n-${i}`,
      title: `Node ${i}`,
      type: "concept" as const,
      x: i * 0.1,
      y: i * 0.1,
      degree: i % 5, // vary degree
    }));
  }

  it("all edges visible when nodeCount ≤ 150 (threshold=0)", () => {
    // 4 nodes → threshold 0 → no edge should be hidden
    const graph = buildGraphologyGraph(NODES, EDGES);
    graph.forEachEdge((_key, attrs) => {
      expect(attrs["hidden"]).toBe(false);
    });
  });

  it("edges below threshold are hidden on graphs with n > 150", () => {
    // 200 nodes → threshold 0.12
    const nodes200 = makeNodes(200);
    // Two edges: one strong (weight much higher than min → high normalizedWeight),
    // one weak (weight = min → normalizedWeight = 0, below 0.12).
    const edges: GraphEdge[] = [
      { source: "n-0", target: "n-1", weight: 100 }, // strong
      { source: "n-0", target: "n-2", weight: 1 },   // weak (min weight → nw=0)
    ];
    const graph = buildGraphologyGraph(nodes200, edges);

    const strongKey = graph.edge("n-0", "n-1");
    const weakKey = graph.edge("n-0", "n-2");

    // Strong edge: normalizedWeight = 1.0 → not hidden
    expect(graph.getEdgeAttribute(strongKey, "hidden")).toBe(false);
    // Weak edge: normalizedWeight = 0.0 < 0.12 → hidden
    expect(graph.getEdgeAttribute(weakKey, "hidden")).toBe(true);
  });

  it("normalizedWeight is stored on each edge for reducer access", () => {
    const graph = buildGraphologyGraph(NODES, EDGES);
    graph.forEachEdge((_key, attrs) => {
      const nw = attrs["normalizedWeight"] as number;
      expect(typeof nw).toBe("number");
      expect(nw).toBeGreaterThanOrEqual(0);
      expect(nw).toBeLessThanOrEqual(1);
    });
  });
});

// ─── B3-LOOK GL2: hub forceLabel selection ───────────────────────────────────

describe("computeTopKHubs — GL2 top-K by degree", () => {
  it("returns empty set for empty node list", () => {
    expect(computeTopKHubs([])).toEqual(new Set());
  });

  it("returns all nodes when n ≤ K (K = min(10, ceil(n*0.02)))", () => {
    // 4 nodes → K = min(10, ceil(4*0.02)) = min(10, 1) = 1
    // Only the single highest-degree node should be in the hub set
    const hubs = computeTopKHubs(NODES);
    // node-1 has degree 2 — highest in NODES fixture
    expect(hubs.has("node-1")).toBe(true);
    expect(hubs.size).toBe(1);
  });

  it("selects the K nodes with the highest degree", () => {
    const nodes: GraphNode[] = [
      { id: "a", title: "A", type: null, x: 0, y: 0, degree: 50 },
      { id: "b", title: "B", type: null, x: 0, y: 0, degree: 30 },
      { id: "c", title: "C", type: null, x: 0, y: 0, degree: 10 },
      { id: "d", title: "D", type: null, x: 0, y: 0, degree: 5 },
      { id: "e", title: "E", type: null, x: 0, y: 0, degree: 1 },
    ];
    // K = min(10, ceil(5*0.02)) = min(10, 1) = 1
    const hubs = computeTopKHubs(nodes);
    expect(hubs.has("a")).toBe(true); // highest degree
    expect(hubs.size).toBe(1);
  });

  it("K scales with n (K = ceil(n*0.02) for large n)", () => {
    // 1000 nodes → K = min(10, ceil(1000*0.02)) = min(10, 20) = 10
    const nodes: GraphNode[] = Array.from({ length: 1000 }, (_, i) => ({
      id: `hub-${i}`,
      title: `Node ${i}`,
      type: null as null,
      x: 0,
      y: 0,
      degree: 1000 - i, // descending: hub-0 has degree 1000, hub-999 has degree 1
    }));
    const hubs = computeTopKHubs(nodes);
    expect(hubs.size).toBe(10);
    // The top-10 should be hub-0 through hub-9
    for (let i = 0; i < 10; i++) {
      expect(hubs.has(`hub-${i}`)).toBe(true);
    }
    // hub-10 should NOT be in the hub set
    expect(hubs.has("hub-10")).toBe(false);
  });

  it("hub nodes get forceLabel=true in built graph", () => {
    // Use a 500-node synthetic graph so K = min(10, ceil(500*0.02)) = 10
    const nodes: GraphNode[] = Array.from({ length: 500 }, (_, i) => ({
      id: `n-${i}`,
      title: `Node ${i}`,
      type: null as null,
      x: i * 0.1,
      y: 0,
      degree: 500 - i, // n-0 is highest-degree hub
    }));
    const graph = buildGraphologyGraph(nodes, []);
    // Top-10 by degree (n-0 .. n-9) should have forceLabel:true
    for (let i = 0; i < 10; i++) {
      expect(graph.getNodeAttribute(`n-${i}`, "forceLabel")).toBe(true);
    }
    // n-10 should NOT have forceLabel
    expect(graph.getNodeAttribute("n-10", "forceLabel")).toBe(false);
  });
});

// ─── B3-LOOK GL3: densityScale function ──────────────────────────────────────

describe("densityScale — GL3 node size scaling", () => {
  it("returns 1.0 for n=0 (guard — no division by zero)", () => {
    expect(densityScale(0)).toBe(1.0);
  });

  it("returns 1.0 for n=1 (single node)", () => {
    expect(densityScale(1)).toBe(1.0);
  });

  it("returns 1.0 for small graphs (n ≤ 150)", () => {
    // sqrt(150/150) = 1.0 → clamp(1.0, 0.4, 1.0) = 1.0
    expect(densityScale(150)).toBeCloseTo(1.0, 5);
  });

  it("returns a value < 1.0 for n > 150", () => {
    expect(densityScale(600)).toBeLessThan(1.0);
  });

  it("returns ~0.39 for n=986 (representative large wiki)", () => {
    // sqrt(150/986) ≈ 0.390 → clamp to [0.4, 1.0] → 0.4
    const scale = densityScale(986);
    expect(scale).toBeCloseTo(0.4, 2);
  });

  it("is clamped to 0.4 minimum for very large graphs", () => {
    expect(densityScale(10_000)).toBe(0.4);
  });

  it("is never greater than 1.0", () => {
    for (const n of [1, 10, 50, 100, 150, 500, 1000, 5000]) {
      expect(densityScale(n)).toBeLessThanOrEqual(1.0);
    }
  });

  it("is never less than 0.4", () => {
    for (const n of [151, 500, 1000, 5000, 100_000]) {
      expect(densityScale(n)).toBeGreaterThanOrEqual(0.4);
    }
  });
});

// ─── B3-LOOK GL3: node sizes are smaller on large graphs ─────────────────────

describe("buildGraphologyGraph — GL3 density scaling applied to node sizes", () => {
  it("node sizes are 1.0× at n=4 (small graph — no shrinkage)", () => {
    // densityScale(4) = clamp(sqrt(150/4), 0.4, 1.0) = clamp(6.12, 0.4, 1.0) = 1.0
    // So sizes should be in the full MIN_R..MAX_R range
    const graph = buildGraphologyGraph(NODES, EDGES);
    graph.forEachNode((_id, attrs) => {
      const size = attrs["size"] as number;
      // With scale=1.0, sizes lie in [MIN_R, MAX_R] = [2.5, 11]
      // Floor is 2.0 (NODE_SIZE_FLOOR) but with scale=1.0, nothing falls below 2.5
      expect(size).toBeGreaterThanOrEqual(2.0);
      expect(size).toBeLessThanOrEqual(11.0);
    });
  });

  it("node sizes are smaller on a 600-node graph than on a 4-node graph", () => {
    // Build two graphs with the same degree distribution, different counts.
    // The 600-node graph should produce smaller sizes because densityScale(600) < 1.
    const largeNodes: GraphNode[] = Array.from({ length: 600 }, (_, i) => ({
      id: `big-${i}`,
      title: `Node ${i}`,
      type: null as null,
      x: 0,
      y: 0,
      degree: 5, // uniform degree so size = MID_R * scale
    }));
    const graphLarge = buildGraphologyGraph(largeNodes, []);

    const graphSmall = buildGraphologyGraph(NODES, EDGES);

    // A degree-5 node on the small graph (4 nodes) will be sized at MID_R * 1.0
    // A degree-5 node on the large graph (600 nodes) will be sized at MID_R * scale(600)
    // scale(600) = clamp(sqrt(150/600), 0.4, 1.0) = clamp(0.5, 0.4, 1.0) = 0.5
    const largeSize = graphLarge.getNodeAttribute("big-0", "size") as number;
    const smallDeg2Size = graphSmall.getNodeAttribute("node-1", "size") as number; // degree=2

    // The large-graph nodes should be smaller (scale 0.5 vs 1.0)
    expect(largeSize).toBeLessThan(smallDeg2Size * 1.0);
  });

  it("all node sizes respect the 2px floor even after scaling", () => {
    // 10000 nodes → densityScale = 0.4 (minimum clamp)
    // Even with MIN_R * 0.4 = 2.5 * 0.4 = 1.0, the floor kicks in → 2.0
    const hugeNodes: GraphNode[] = Array.from({ length: 10_000 }, (_, i) => ({
      id: `h-${i}`,
      title: `Node ${i}`,
      type: null as null,
      x: 0,
      y: 0,
      degree: 0, // min degree → smallest possible radius before floor
    }));
    const graph = buildGraphologyGraph(hugeNodes, []);
    graph.forEachNode((_id, attrs) => {
      expect(attrs["size"] as number).toBeGreaterThanOrEqual(2.0);
    });
  });
});
