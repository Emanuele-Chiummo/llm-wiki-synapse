/**
 * graphInsights.test.ts — unit tests for computeGraphInsights (F4, G-P1-5).
 *
 * Coverage:
 *   A. Surprising connections
 *      A1. Cross-community high-weight edge surfaces as surprising.
 *      A2. Same-community edge does NOT appear.
 *      A3. Below-threshold weight excluded (weight < 3).
 *      A4. Meta nodes (index/log/overview by type and by title) excluded.
 *      A5. Sorted by score descending, capped at 8.
 *      A6. Both endpoints unassigned (-1) excluded.
 *      A7. One endpoint unassigned excluded.
 *      A8. Stable id format: "surprising:{source}:{target}".
 *
 *   B. Knowledge gaps — isolated
 *      B1. Node with degree 0 detected.
 *      B2. Node with degree 1 detected.
 *      B3. Node with degree 2 NOT isolated.
 *      B4. Meta nodes excluded from isolation detection.
 *      B5. Stable id: "gap-isolated:{nodeId}".
 *
 *   C. Knowledge gaps — sparse community
 *      C1. cohesion < 0.15 AND size >= 3 → detected.
 *      C2. cohesion = 0.15 NOT detected (boundary).
 *      C3. cohesion < 0.15 but size < 3 → NOT detected.
 *      C4. Stable id: "gap-sparse:{communityId}".
 *      C5. primaryNodeId points to a member node.
 *
 *   D. Knowledge gaps — bridge
 *      D1. Node with 3+ distinct neighbor communities detected.
 *      D2. Node with 2 neighbor communities NOT detected (below threshold).
 *      D3. Own community excluded from neighbor-community count.
 *      D4. Unassigned (-1) neighbors excluded from count.
 *      D5. Meta nodes excluded from bridge detection.
 *      D6. Stable id: "gap-bridge:{nodeId}".
 *
 *   E. isMetaNode helper
 *      E1. type="index" → meta.
 *      E2. type="log" → meta.
 *      E3. title="index" → meta.
 *      E4. title="log" → meta.
 *      E5. title="overview" → meta.
 *      E6. title="Index" (capitalised) → meta (case-insensitive).
 *      E7. Regular node → not meta.
 *
 *   F. total count
 *      F1. total = sum of all four groups.
 *
 * INVARIANT I2: no layout or community computation — all community fields
 *   are pre-set in test fixtures, matching server-supplied values.
 */

import { describe, it, expect } from "vitest";
import { computeGraphInsights, isMetaNode } from "../components/graph/graphInsights";
import type { GraphNode, GraphEdge, GraphCommunity } from "../api/types";

// ─── Helpers ──────────────────────────────────────────────────────────────────

function node(
  id: string,
  title: string,
  type: string | null,
  community: number,
  degree: number,
): GraphNode {
  return { id, title, type, x: 0, y: 0, community, degree };
}

function edge(source: string, target: string, weight: number): GraphEdge {
  return { source, target, weight };
}

function community(id: number, size: number, cohesion: number): GraphCommunity {
  return { id, size, cohesion };
}

// ─── A. Surprising connections ────────────────────────────────────────────────

describe("computeGraphInsights — surprising connections", () => {
  const nA = node("a", "Alpha", "concept", 0, 5);
  const nB = node("b", "Beta", "entity", 1, 5);
  const nC = node("c", "Gamma", "concept", 1, 5);
  const nD = node("d", "Delta", "entity", 0, 5);

  it("A1: cross-community edge with weight >= 3 appears in surprising", () => {
    const edges = [edge("a", "b", 4)]; // community 0 → 1, weight 4
    const result = computeGraphInsights([nA, nB, nC, nD], edges, []);
    expect(result.surprising).toHaveLength(1);
    expect(result.surprising[0]?.sourceNodeId).toBe("a");
    expect(result.surprising[0]?.targetNodeId).toBe("b");
  });

  it("A2: same-community edge does NOT appear in surprising", () => {
    const edges = [edge("a", "d", 10)]; // both community 0
    const result = computeGraphInsights([nA, nB, nC, nD], edges, []);
    expect(result.surprising).toHaveLength(0);
  });

  it("A3: cross-community edge with weight < 3 excluded", () => {
    const edges = [edge("a", "b", 2.9)];
    const result = computeGraphInsights([nA, nB, nC, nD], edges, []);
    expect(result.surprising).toHaveLength(0);
  });

  it("A4a: edge where source is meta (type=index) excluded", () => {
    const metaNode = node("idx", "Index", "index", 2, 5);
    const edges = [edge("idx", "b", 8)];
    const result = computeGraphInsights([metaNode, nB], edges, []);
    expect(result.surprising).toHaveLength(0);
  });

  it("A4b: edge where source has title=overview excluded", () => {
    const overviewNode = node("ov", "overview", "concept", 2, 5);
    const edges = [edge("ov", "b", 8)];
    const result = computeGraphInsights([overviewNode, nB], edges, []);
    expect(result.surprising).toHaveLength(0);
  });

  it("A4c: edge where target is meta (title=log) excluded", () => {
    const logNode = node("lg", "log", "concept", 2, 5);
    const edges = [edge("a", "lg", 8)];
    const result = computeGraphInsights([nA, logNode], edges, []);
    expect(result.surprising).toHaveLength(0);
  });

  it("A5: results sorted by score descending, capped at 8", () => {
    // Build 10 cross-community edges with distinct weights
    const nodeList: GraphNode[] = [
      node("src", "Src", "concept", 0, 10),
      ...Array.from({ length: 10 }, (_, i) => node(`t${i}`, `T${i}`, "entity", 1, 5)),
    ];
    const edgeList: GraphEdge[] = Array.from(
      { length: 10 },
      (_, i) => edge("src", `t${i}`, 3 + i), // weights 3..12
    );
    const result = computeGraphInsights(nodeList, edgeList, []);
    expect(result.surprising).toHaveLength(8);
    // First item has highest score
    const scores = result.surprising.map((s) => s.score);
    for (let i = 1; i < scores.length; i++) {
      expect(scores[i - 1]!).toBeGreaterThanOrEqual(scores[i]!);
    }
  });

  it("A6: both endpoints with community -1 excluded", () => {
    const nX = node("x", "X", "concept", -1, 5);
    const nY = node("y", "Y", "concept", -1, 5);
    const result = computeGraphInsights([nX, nY], [edge("x", "y", 10)], []);
    expect(result.surprising).toHaveLength(0);
  });

  it("A7: one endpoint with community -1 excluded", () => {
    const nX = node("x", "X", "concept", -1, 5);
    const nY = node("y", "Y", "concept", 1, 5);
    const result = computeGraphInsights([nX, nY], [edge("x", "y", 10)], []);
    expect(result.surprising).toHaveLength(0);
  });

  it("A8: stable id is 'surprising:{source}:{target}'", () => {
    const result = computeGraphInsights([nA, nB], [edge("a", "b", 5)], []);
    expect(result.surprising[0]?.id).toBe("surprising:a:b");
  });
});

// ─── B. Isolated nodes ────────────────────────────────────────────────────────

describe("computeGraphInsights — gap-isolated", () => {
  it("B1: node with degree 0 detected as isolated", () => {
    const n = node("orphan", "Orphan", "concept", 0, 0);
    const result = computeGraphInsights([n], [], []);
    const ids = result.gapIsolated.map((i) => i.nodeId);
    expect(ids).toContain("orphan");
  });

  it("B2: node with degree 1 detected as isolated", () => {
    const n = node("leaf", "Leaf", "entity", 0, 1);
    const result = computeGraphInsights([n], [], []);
    const ids = result.gapIsolated.map((i) => i.nodeId);
    expect(ids).toContain("leaf");
  });

  it("B3: node with degree 2 NOT isolated", () => {
    const n = node("hub", "Hub", "concept", 0, 2);
    const result = computeGraphInsights([n], [], []);
    const ids = result.gapIsolated.map((i) => i.nodeId);
    expect(ids).not.toContain("hub");
  });

  it("B4: meta node (type=log) excluded from isolation detection", () => {
    const metaLog = node("lg", "log", "log", 0, 0);
    const result = computeGraphInsights([metaLog], [], []);
    expect(result.gapIsolated).toHaveLength(0);
  });

  it("B4b: meta node by title 'overview' excluded", () => {
    const ov = node("ov", "Overview", "concept", 0, 0);
    const result = computeGraphInsights([ov], [], []);
    expect(result.gapIsolated).toHaveLength(0);
  });

  it("B5: stable id is 'gap-isolated:{nodeId}'", () => {
    const n = node("lone", "Lone", "concept", 0, 0);
    const result = computeGraphInsights([n], [], []);
    expect(result.gapIsolated[0]?.id).toBe("gap-isolated:lone");
  });
});

// ─── C. Sparse communities ────────────────────────────────────────────────────

describe("computeGraphInsights — gap-sparse", () => {
  const nodesForCommunity = [
    node("n1", "N1", "concept", 5, 2),
    node("n2", "N2", "concept", 5, 2),
    node("n3", "N3", "entity", 5, 2),
  ];

  it("C1: cohesion < 0.15 AND size >= 3 → detected", () => {
    const result = computeGraphInsights(nodesForCommunity, [], [community(5, 4, 0.1)]);
    const ids = result.gapSparse.map((g) => g.communityId);
    expect(ids).toContain(5);
  });

  it("C2: cohesion = 0.15 NOT detected (boundary — not strictly less)", () => {
    const result = computeGraphInsights(nodesForCommunity, [], [community(5, 4, 0.15)]);
    expect(result.gapSparse).toHaveLength(0);
  });

  it("C3: cohesion < 0.15 but size < 3 → NOT detected", () => {
    const result = computeGraphInsights(nodesForCommunity, [], [community(5, 2, 0.05)]);
    expect(result.gapSparse).toHaveLength(0);
  });

  it("C4: stable id is 'gap-sparse:{communityId}'", () => {
    const result = computeGraphInsights(nodesForCommunity, [], [community(5, 3, 0.08)]);
    expect(result.gapSparse[0]?.id).toBe("gap-sparse:5");
  });

  it("C5: primaryNodeId points to a member node of that community", () => {
    const result = computeGraphInsights(nodesForCommunity, [], [community(5, 3, 0.08)]);
    const item = result.gapSparse[0];
    expect(item).toBeDefined();
    const memberIds = nodesForCommunity.map((n) => n.id);
    expect(memberIds).toContain(item!.primaryNodeId);
  });

  it("C5b: primaryNodeId is null when no member node exists for the community", () => {
    // community id 99, but no node has community=99
    const result = computeGraphInsights(nodesForCommunity, [], [community(99, 5, 0.05)]);
    expect(result.gapSparse[0]?.primaryNodeId).toBeNull();
  });
});

// ─── D. Bridge nodes ─────────────────────────────────────────────────────────

describe("computeGraphInsights — gap-bridge", () => {
  // Bridge: node "b" connects community 0,1,2,3
  const nBridge = node("b", "Bridge", "concept", 10, 4);
  const nA = node("a", "A", "concept", 0, 1);
  const nB2 = node("b2", "B2", "concept", 1, 1);
  const nC = node("c", "C", "concept", 2, 1);
  const nD = node("d", "D", "entity", 3, 1);

  const bridgeEdges: GraphEdge[] = [
    edge("b", "a", 1),
    edge("b", "b2", 1),
    edge("b", "c", 1),
    edge("b", "d", 1),
  ];

  it("D1: node with 4 distinct neighbor communities (>= 3) detected", () => {
    const result = computeGraphInsights([nBridge, nA, nB2, nC, nD], bridgeEdges, []);
    const ids = result.gapBridge.map((g) => g.nodeId);
    expect(ids).toContain("b");
  });

  it("D2: node with only 2 neighbor communities NOT detected", () => {
    const nHub = node("hub", "Hub", "concept", 10, 2);
    const nX = node("x", "X", "concept", 0, 1);
    const nY = node("y", "Y", "concept", 1, 1);
    const result = computeGraphInsights(
      [nHub, nX, nY],
      [edge("hub", "x", 1), edge("hub", "y", 1)],
      [],
    );
    expect(result.gapBridge).toHaveLength(0);
  });

  it("D3: own community excluded from neighbor-community count", () => {
    // Bridge node community=0; neighbors in community 0,1,2 — only 1,2 should count
    const nHub = node("hub", "Hub", "concept", 0, 3);
    const nSameCom = node("s", "S", "concept", 0, 1); // same community
    const nOther1 = node("o1", "O1", "concept", 1, 1);
    const nOther2 = node("o2", "O2", "concept", 2, 1);
    const result = computeGraphInsights(
      [nHub, nSameCom, nOther1, nOther2],
      [edge("hub", "s", 1), edge("hub", "o1", 1), edge("hub", "o2", 1)],
      [],
    );
    // Only 2 distinct OTHER communities — below threshold of 3
    expect(result.gapBridge).toHaveLength(0);
  });

  it("D4: neighbors with community -1 excluded from count", () => {
    const nHub = node("hub", "Hub", "concept", 0, 3);
    const nUnassigned = node("u", "U", "concept", -1, 1);
    const nOther1 = node("o1", "O1", "concept", 1, 1);
    const nOther2 = node("o2", "O2", "concept", 2, 1);
    const result = computeGraphInsights(
      [nHub, nUnassigned, nOther1, nOther2],
      [
        edge("hub", "u", 1), // excluded (community -1)
        edge("hub", "o1", 1),
        edge("hub", "o2", 1),
      ],
      [],
    );
    // Only communities 1 and 2 count → 2 < 3 → no bridge
    expect(result.gapBridge).toHaveLength(0);
  });

  it("D5: meta node excluded from bridge detection", () => {
    const metaIndex = node("idx", "index", "concept", 10, 4);
    const result = computeGraphInsights(
      [metaIndex, nA, nB2, nC, nD],
      [edge("idx", "a", 1), edge("idx", "b2", 1), edge("idx", "c", 1), edge("idx", "d", 1)],
      [],
    );
    const ids = result.gapBridge.map((g) => g.nodeId);
    expect(ids).not.toContain("idx");
  });

  it("D6: stable id is 'gap-bridge:{nodeId}'", () => {
    const result = computeGraphInsights([nBridge, nA, nB2, nC, nD], bridgeEdges, []);
    const bridgeItem = result.gapBridge.find((g) => g.nodeId === "b");
    expect(bridgeItem?.id).toBe("gap-bridge:b");
  });
});

// ─── E. isMetaNode ────────────────────────────────────────────────────────────

describe("isMetaNode helper", () => {
  it("E1: type='index' → meta", () => {
    expect(isMetaNode(node("n", "Something", "index", 0, 1))).toBe(true);
  });

  it("E2: type='log' → meta", () => {
    expect(isMetaNode(node("n", "Something", "log", 0, 1))).toBe(true);
  });

  it("E3: title='index' → meta", () => {
    expect(isMetaNode(node("n", "index", "concept", 0, 1))).toBe(true);
  });

  it("E4: title='log' → meta", () => {
    expect(isMetaNode(node("n", "log", "entity", 0, 1))).toBe(true);
  });

  it("E5: title='overview' → meta", () => {
    expect(isMetaNode(node("n", "overview", "concept", 0, 1))).toBe(true);
  });

  it("E6: title='Index' (capitalised) → meta (case-insensitive)", () => {
    expect(isMetaNode(node("n", "Index", "concept", 0, 1))).toBe(true);
  });

  it("E7: regular node → not meta", () => {
    expect(isMetaNode(node("n", "Temperature Scaling", "concept", 0, 5))).toBe(false);
  });

  it("E7b: type=null and non-meta title → not meta", () => {
    expect(isMetaNode(node("n", "My Note", null, 0, 2))).toBe(false);
  });
});

// ─── F. Total count ───────────────────────────────────────────────────────────

describe("computeGraphInsights — total count", () => {
  it("F1: total equals sum of all four groups", () => {
    const nodes: GraphNode[] = [
      node("a", "A", "concept", 0, 5),
      node("b", "B", "entity", 1, 5),
      node("orphan", "Orphan", "concept", 2, 0),
      node("bridge", "Bridge", "concept", 10, 4),
      node("x", "X", "concept", 3, 1),
      node("y", "Y", "concept", 4, 1),
      node("z", "Z", "concept", 5, 1),
    ];
    const edges: GraphEdge[] = [
      edge("a", "b", 5),
      edge("bridge", "x", 1),
      edge("bridge", "y", 1),
      edge("bridge", "z", 1),
    ];
    const communities: GraphCommunity[] = [
      community(2, 3, 0.05), // sparse
    ];
    const result = computeGraphInsights(nodes, edges, communities);
    const expectedTotal =
      result.surprising.length +
      result.gapIsolated.length +
      result.gapSparse.length +
      result.gapBridge.length;
    expect(result.total).toBe(expectedTotal);
  });
});
