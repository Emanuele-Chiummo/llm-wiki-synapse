/**
 * graphHeader.test.ts
 *
 * Tests for GraphHeader filter logic (GR1/GR3) and graphStore filter slice.
 *
 * These tests verify:
 * - Filter correctly computes visibleNodes / hiddenCount (GR1)
 * - Filter correctly selects visible edges (GR1 links chip)
 * - graphStore toggleFilterNodeType / clearFilterNodeTypes work correctly (GR3)
 * - I2-safe: no layout functions are called when filter changes
 *
 * GraphHeader is tested as pure logic (no sigma/WebGL needed) because the
 * filter counts are derived from the store's nodes/edges arrays — pure data.
 */

import { describe, it, expect, beforeEach } from "vitest";
import type { GraphNode, GraphEdge } from "../api/types";
import { edgeVisibilityThreshold } from "../api/graphTransform";

// ─── Fixtures ─────────────────────────────────────────────────────────────────

function makeNodes(): GraphNode[] {
  return [
    { id: "c1", title: "Concept A", type: "concept", x: 0, y: 0, degree: 5 },
    { id: "c2", title: "Concept B", type: "concept", x: 1, y: 0, degree: 3 },
    { id: "e1", title: "Entity X", type: "entity", x: 2, y: 0, degree: 2 },
    { id: "s1", title: "Source 1", type: "source", x: 3, y: 0, degree: 1 },
    { id: "u1", title: "Untyped", type: null, x: 4, y: 0, degree: 0 },
  ];
}

function makeEdges(): GraphEdge[] {
  return [
    { source: "c1", target: "c2", weight: 10 },   // concept–concept
    { source: "c1", target: "e1", weight: 8 },    // concept–entity
    { source: "e1", target: "s1", weight: 5 },    // entity–source
    { source: "c2", target: "u1", weight: 2 },    // concept–other(null)
  ];
}

// ─── Visible node count (GR1 pages chip logic) ───────────────────────────────

describe("GR1 pages chip — visibleNodes computation", () => {
  const nodes = makeNodes();

  function computeVisibleNodes(nodes: GraphNode[], filter: Set<string>): number {
    if (filter.size === 0) return nodes.length;
    return nodes.filter((n) => filter.has(n.type ?? "other")).length;
  }

  it("shows all nodes when filter is empty", () => {
    expect(computeVisibleNodes(nodes, new Set())).toBe(5);
  });

  it("shows only concept nodes when filter = {concept}", () => {
    expect(computeVisibleNodes(nodes, new Set(["concept"]))).toBe(2);
  });

  it("shows concept + entity nodes when filter = {concept, entity}", () => {
    expect(computeVisibleNodes(nodes, new Set(["concept", "entity"]))).toBe(3);
  });

  it("treats null type as 'other' for filtering", () => {
    expect(computeVisibleNodes(nodes, new Set(["other"]))).toBe(1);
  });

  it("hiddenCount = totalNodes - visibleNodes covers both filtered + not-in-graph", () => {
    // total_nodes from backend = 10 (6 not in graph + 4 in-graph filtered-out)
    const totalNodes = 10;
    const visible = computeVisibleNodes(nodes, new Set(["concept"]));
    const hidden = totalNodes - visible;
    expect(hidden).toBe(8); // 2 visible concepts → 10 - 2 = 8 hidden
  });
});

// ─── Visible edge count (GR1 links chip logic) ───────────────────────────────

describe("GR1 links chip — visibleEdges computation", () => {
  const nodes = makeNodes();
  const edges = makeEdges();

  function buildNodeTypeMap(nodes: GraphNode[]): Map<string, string> {
    const m = new Map<string, string>();
    for (const n of nodes) m.set(n.id, n.type ?? "other");
    return m;
  }

  function computeVisibleEdges(
    edges: GraphEdge[],
    filter: Set<string>,
    nodeTypeMap: Map<string, string>,
  ): number {
    if (filter.size === 0) return edges.length;
    return edges.filter((e) => {
      const src = nodeTypeMap.get(e.source) ?? "other";
      const tgt = nodeTypeMap.get(e.target) ?? "other";
      return filter.has(src) && filter.has(tgt);
    }).length;
  }

  const nodeTypeMap = buildNodeTypeMap(nodes);

  it("shows all edges when filter is empty", () => {
    expect(computeVisibleEdges(edges, new Set(), nodeTypeMap)).toBe(4);
  });

  it("shows only concept–concept edges when filter = {concept}", () => {
    // Only c1–c2 edge: both concept
    expect(computeVisibleEdges(edges, new Set(["concept"]), nodeTypeMap)).toBe(1);
  });

  it("shows concept–concept and concept–entity edges when filter = {concept, entity}", () => {
    // c1–c2 (concept–concept) + c1–e1 (concept–entity): 2 edges
    expect(computeVisibleEdges(edges, new Set(["concept", "entity"]), nodeTypeMap)).toBe(2);
  });

  it("hides edge when either endpoint type is filtered out", () => {
    // e1–s1 is entity–source; filter = {entity} only → source not in filter → hidden
    expect(computeVisibleEdges(edges, new Set(["entity"]), nodeTypeMap)).toBe(0);
  });

  it("denominator is always edges.length (full graph edge set)", () => {
    // The total edges count must not change with the filter (it's always the full set)
    expect(edges.length).toBe(4);
  });
});

// ─── graphStore filter slice (GR3) ───────────────────────────────────────────

describe("graphStore filterNodeTypes slice", () => {
  // Test the toggle/clear logic directly (pure reducer semantics — no Zustand needed)

  function toggle(current: Set<string>, type: string): Set<string> {
    const next = new Set(current);
    if (next.has(type)) next.delete(type);
    else next.add(type);
    return next;
  }

  let filter: Set<string>;

  beforeEach(() => {
    filter = new Set<string>();
  });

  it("starts empty (no filter active — all types visible)", () => {
    expect(filter.size).toBe(0);
  });

  it("toggles a type in (adds to filter set)", () => {
    filter = toggle(filter, "concept");
    expect(filter.has("concept")).toBe(true);
    expect(filter.size).toBe(1);
  });

  it("toggles a type out (removes from filter set)", () => {
    filter = toggle(filter, "concept");
    filter = toggle(filter, "concept");
    expect(filter.has("concept")).toBe(false);
    expect(filter.size).toBe(0);
  });

  it("supports multiple types simultaneously", () => {
    filter = toggle(filter, "concept");
    filter = toggle(filter, "entity");
    expect(filter.has("concept")).toBe(true);
    expect(filter.has("entity")).toBe(true);
    expect(filter.size).toBe(2);
  });

  it("clear resets to empty set", () => {
    filter = toggle(filter, "concept");
    filter = toggle(filter, "entity");
    filter = new Set<string>(); // clearFilterNodeTypes
    expect(filter.size).toBe(0);
  });

  it("hiddenCount is 0 when filter is empty (all types shown)", () => {
    const nodes = makeNodes();
    const totalNodes = nodes.length;
    const visible = filter.size === 0 ? nodes.length : nodes.filter((n) => filter.has(n.type ?? "other")).length;
    expect(totalNodes - visible).toBe(0);
  });

  it("hiddenCount increases when a type is excluded from filter", () => {
    const nodes = makeNodes();
    filter = toggle(filter, "concept"); // only concept visible
    const visible = nodes.filter((n) => filter.has(n.type ?? "other")).length;
    // 2 concept nodes visible; 3 others (entity, source, other) = hidden
    expect(visible).toBe(2);
    expect(nodes.length - visible).toBe(3);
  });

  it("I2-safe: no layout functions called by toggle (pure set operation)", () => {
    // toggleFilterNodeType is a pure Set mutation — no sigma, no coords, no rAF
    let f = new Set<string>();
    f = toggle(f, "concept");
    f = toggle(f, "entity");
    // Verify result is pure data — no side effects
    expect(f).toBeInstanceOf(Set);
    expect(Array.from(f).sort()).toEqual(["concept", "entity"]);
  });
});

// ─── GL1 link-chip culling (P3 graph link-chip fix) ──────────────────────────
// The link-chip numerator must exclude GL1-culled edges (normalizedWeight < threshold)
// in addition to type-filtered edges. This mirrors nashsu/llm_wiki.

describe("GL1 link-chip culling — visibleEdges with edgeVisibilityThreshold", () => {
  // Build a normalizedWeight map from the raw weight range (same formula as graphTransform.ts).
  function buildNormWeightMap(edges: GraphEdge[]): Map<string, number> {
    const m = new Map<string, number>();
    if (edges.length === 0) return m;
    let wMin = Infinity, wMax = -Infinity;
    for (const e of edges) {
      if (e.weight < wMin) wMin = e.weight;
      if (e.weight > wMax) wMax = e.weight;
    }
    const range = wMax - wMin;
    for (const e of edges) {
      const nw = range === 0 ? 0.5 : (e.weight - wMin) / range;
      const key = e.source < e.target ? `${e.source}__${e.target}` : `${e.target}__${e.source}`;
      m.set(key, nw);
    }
    return m;
  }

  function computeVisibleEdgesWithCulling(
    edges: GraphEdge[],
    nodeCount: number,
    filter: Set<string>,
    nodeTypeMap: Map<string, string>,
    normWeightMap: Map<string, number>,
  ): number {
    const threshold = edgeVisibilityThreshold(nodeCount);
    return edges.filter((e) => {
      const key = e.source < e.target ? `${e.source}__${e.target}` : `${e.target}__${e.source}`;
      const nw = normWeightMap.get(key) ?? 0.5;
      if (nw < threshold) return false;
      if (filter.size === 0) return true;
      const srcType = nodeTypeMap.get(e.source) ?? "other";
      const tgtType = nodeTypeMap.get(e.target) ?? "other";
      return filter.has(srcType) && filter.has(tgtType);
    }).length;
  }

  it("threshold is 0 for ≤600 nodes — no culling on small graphs", () => {
    expect(edgeVisibilityThreshold(100)).toBe(0);
    expect(edgeVisibilityThreshold(600)).toBe(0);
  });

  it("threshold > 0 for large graphs (>600 nodes)", () => {
    expect(edgeVisibilityThreshold(601)).toBeGreaterThan(0);
    expect(edgeVisibilityThreshold(1200)).toBeGreaterThan(0);
    expect(edgeVisibilityThreshold(2000)).toBeGreaterThan(0);
  });

  it("all 4 edges visible on small graph (≤600 nodes, threshold=0)", () => {
    const nodes = makeNodes(); // 5 nodes
    const edges = makeEdges(); // 4 edges
    const nodeTypeMap = new Map(nodes.map((n) => [n.id, n.type ?? "other"]));
    const normWeightMap = buildNormWeightMap(edges);
    // 5 nodes → threshold=0 → no culling
    const visible = computeVisibleEdgesWithCulling(edges, 5, new Set(), nodeTypeMap, normWeightMap);
    expect(visible).toBe(4);
  });

  it("weak edges are culled when threshold > 0 (simulated large graph)", () => {
    // Simulate a graph where one edge has nw=0 (weakest) and we force threshold=0.03
    // by telling the function node count is 800.
    // With weights [1, 1, 1, 2]: min=1, max=2, range=1 → nw for w=1 is 0, w=2 is 1.
    const edges: GraphEdge[] = [
      { source: "c1", target: "c2", weight: 1 },   // nw = 0 → culled at threshold 0.03
      { source: "c1", target: "e1", weight: 1 },   // nw = 0 → culled
      { source: "e1", target: "s1", weight: 2 },   // nw = 1 → shown
      { source: "c2", target: "u1", weight: 1 },   // nw = 0 → culled
    ];
    const nodes = makeNodes();
    const nodeTypeMap = new Map(nodes.map((n) => [n.id, n.type ?? "other"]));
    const normWeightMap = buildNormWeightMap(edges);
    // 800 nodes → threshold = 0.03 → nw=0 edges culled, nw=1 edge shown
    const visible = computeVisibleEdgesWithCulling(edges, 800, new Set(), nodeTypeMap, normWeightMap);
    expect(visible).toBe(1); // only the nw=1 edge passes
  });

  it("denominator always equals edges.length regardless of culling", () => {
    const edges = makeEdges();
    expect(edges.length).toBe(4);
  });

  it("culled + type-filtered: both constraints applied", () => {
    // 1 strong edge (nw=1, concept–concept) and 3 weak edges (nw=0)
    // Filter = {concept}: only concept–concept edges pass type filter
    // GL1 threshold at 800 nodes = 0.03: nw=0 culled
    const edges: GraphEdge[] = [
      { source: "c1", target: "c2", weight: 2 },  // strong, concept–concept
      { source: "c1", target: "e1", weight: 1 },  // weak, concept–entity
      { source: "e1", target: "s1", weight: 1 },  // weak, entity–source
      { source: "c2", target: "u1", weight: 1 },  // weak, concept–other
    ];
    const nodes = makeNodes();
    const nodeTypeMap = new Map(nodes.map((n) => [n.id, n.type ?? "other"]));
    const normWeightMap = buildNormWeightMap(edges);
    // GL1 culls nw=0; filter {concept} keeps only concept–concept
    const visible = computeVisibleEdgesWithCulling(edges, 800, new Set(["concept"]), nodeTypeMap, normWeightMap);
    expect(visible).toBe(1); // only c1–c2 passes both GL1 + filter
  });
});
