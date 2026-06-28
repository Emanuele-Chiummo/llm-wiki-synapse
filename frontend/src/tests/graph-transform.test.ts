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
import { buildGraphologyGraph } from "../api/graphTransform";
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
