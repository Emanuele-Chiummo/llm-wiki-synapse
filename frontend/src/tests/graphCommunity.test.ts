/**
 * graphCommunity.test.ts — unit tests for graph community coloring (F4, v0.6).
 *
 * Coverage:
 *   A. COMMUNITY_PALETTE + colorForCommunity — correct palette mapping, cycling, unassigned (-1).
 *   B. GraphNode community field passes through graphTransform verbatim (I2: no recompute).
 *   C. GraphStore carries communities from setGraph; selectCommunities selector.
 *   D. GraphResponse community types contract (GraphCommunity shape).
 *   E. LOW_COHESION_THRESHOLD contract — communities below threshold flagged.
 *
 * INVARIANT I2: community ids are ALWAYS read from the server response.
 *   No client-side community detection or Louvain runs are invoked in any test.
 */

import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  COMMUNITY_PALETTE,
  LOW_COHESION_THRESHOLD,
  colorForCommunity,
} from "../components/graphPalette";
import { buildGraphologyGraph } from "../api/graphTransform";
import { useGraphStore, selectCommunities } from "../store/graphStore";
import type { GraphNode, GraphEdge, GraphCommunity } from "../api/types";

// ─── A. COMMUNITY_PALETTE + colorForCommunity ─────────────────────────────────

describe("COMMUNITY_PALETTE — 12-color categorical palette (§COMMUNITY-PALETTE)", () => {
  it("has exactly 12 entries", () => {
    expect(COMMUNITY_PALETTE).toHaveLength(12);
  });

  it("all entries are valid 7-char hex strings (#rrggbb)", () => {
    for (const color of COMMUNITY_PALETTE) {
      expect(color).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });

  it("all 12 entries are distinct (no duplicates)", () => {
    const unique = new Set(COMMUNITY_PALETTE);
    expect(unique.size).toBe(12);
  });
});

describe("colorForCommunity — palette mapping (I2: read-only server values)", () => {
  it("returns COMMUNITY_PALETTE[0] for community 0 (largest)", () => {
    expect(colorForCommunity(0)).toBe(COMMUNITY_PALETTE[0]);
  });

  it("returns COMMUNITY_PALETTE[11] for community 11 (last in palette)", () => {
    expect(colorForCommunity(11)).toBe(COMMUNITY_PALETTE[11]);
  });

  it("cycles back to COMMUNITY_PALETTE[0] for community 12 (wraps)", () => {
    expect(colorForCommunity(12)).toBe(COMMUNITY_PALETTE[0]);
  });

  it("cycles correctly for community 25 (25 % 12 = 1)", () => {
    expect(colorForCommunity(25)).toBe(COMMUNITY_PALETTE[1]);
  });

  it("returns DEFAULT_NODE_COLOR (#6e7781) for unassigned community (-1)", () => {
    expect(colorForCommunity(-1)).toBe("#6e7781");
  });

  it("returns DEFAULT_NODE_COLOR for any negative community id", () => {
    expect(colorForCommunity(-99)).toBe("#6e7781");
  });

  it("returns a hex string for every community 0–23 (two full cycles)", () => {
    for (let id = 0; id < 24; id++) {
      const color = colorForCommunity(id);
      expect(color).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });
});

// ─── B. graphTransform passes community through verbatim (I2) ─────────────────

describe("buildGraphologyGraph — community passthrough (I2: no client recompute)", () => {
  const nodes: GraphNode[] = [
    { id: "n1", title: "Alpha", type: "concept", x: 0, y: 0, degree: 1, community: 0 },
    { id: "n2", title: "Beta",  type: "entity",  x: 1, y: 1, degree: 1, community: 3 },
    { id: "n3", title: "Gamma", type: "source",  x: 2, y: 2, degree: 0 }, // no community field
  ];
  const edges: GraphEdge[] = [{ source: "n1", target: "n2", weight: 1 }];

  it("stores community=0 on node n1 verbatim", () => {
    const g = buildGraphologyGraph(nodes, edges);
    expect(g.getNodeAttribute("n1", "community")).toBe(0);
  });

  it("stores community=3 on node n2 verbatim", () => {
    const g = buildGraphologyGraph(nodes, edges);
    expect(g.getNodeAttribute("n2", "community")).toBe(3);
  });

  it("defaults to community=-1 when field is absent (older server, non-breaking)", () => {
    const g = buildGraphologyGraph(nodes, edges);
    expect(g.getNodeAttribute("n3", "community")).toBe(-1);
  });

  it("does NOT call Math.random during community assignment (I2 sentinel)", () => {
    const randomSpy = vi.spyOn(Math, "random");
    buildGraphologyGraph(nodes, edges);
    expect(randomSpy).not.toHaveBeenCalled();
    randomSpy.mockRestore();
  });
});

// ─── C. graphStore carries communities from setGraph ──────────────────────────

describe("graphStore — communities via setGraph + selectCommunities (I3)", () => {
  beforeEach(() => {
    useGraphStore.getState().reset();
  });

  it("selectCommunities returns empty array in initial state", () => {
    const s = useGraphStore.getState();
    expect(selectCommunities(s)).toEqual([]);
  });

  it("setGraph without communities defaults to [] (backward compat)", () => {
    useGraphStore.getState().setGraph([], [], 1, "hit");
    const s = useGraphStore.getState();
    expect(selectCommunities(s)).toEqual([]);
  });

  it("setGraph with communities stores them and selectCommunities returns them", () => {
    const communities: GraphCommunity[] = [
      { id: 0, size: 42, cohesion: 0.85 },
      { id: 1, size: 10, cohesion: 0.05 },
    ];
    useGraphStore.getState().setGraph([], [], 2, "miss", communities);
    const s = useGraphStore.getState();
    const stored = selectCommunities(s);
    expect(stored).toHaveLength(2);
    expect(stored[0]?.id).toBe(0);
    expect(stored[0]?.size).toBe(42);
    expect(stored[0]?.cohesion).toBe(0.85);
    expect(stored[1]?.id).toBe(1);
  });

  it("reset clears communities back to []", () => {
    useGraphStore.getState().setGraph([], [], 1, "hit", [
      { id: 0, size: 5, cohesion: 0.9 },
    ]);
    useGraphStore.getState().reset();
    expect(selectCommunities(useGraphStore.getState())).toEqual([]);
  });
});

// ─── D. GraphCommunity shape contract ────────────────────────────────────────

describe("GraphCommunity shape contract (types.ts)", () => {
  it("a valid GraphCommunity has id (number), size (number), cohesion (number)", () => {
    const c: GraphCommunity = { id: 2, size: 7, cohesion: 0.45 };
    expect(typeof c.id).toBe("number");
    expect(typeof c.size).toBe("number");
    expect(typeof c.cohesion).toBe("number");
  });

  it("community id -1 is a valid value for unassigned communities", () => {
    const unassigned: GraphCommunity = { id: -1, size: 3, cohesion: 0.0 };
    expect(unassigned.id).toBe(-1);
  });
});

// ─── E. LOW_COHESION_THRESHOLD ────────────────────────────────────────────────

describe("LOW_COHESION_THRESHOLD — legend warning logic", () => {
  it("is 0.1", () => {
    expect(LOW_COHESION_THRESHOLD).toBe(0.1);
  });

  it("cohesion = 0.09 is considered low-cohesion (< threshold)", () => {
    const c: GraphCommunity = { id: 0, size: 10, cohesion: 0.09 };
    expect(c.cohesion < LOW_COHESION_THRESHOLD).toBe(true);
  });

  it("cohesion = 0.10 is NOT low-cohesion (= threshold, not strictly less)", () => {
    const c: GraphCommunity = { id: 0, size: 10, cohesion: 0.10 };
    expect(c.cohesion < LOW_COHESION_THRESHOLD).toBe(false);
  });

  it("cohesion = 0.0 is low-cohesion (isolated community)", () => {
    const c: GraphCommunity = { id: 3, size: 1, cohesion: 0.0 };
    expect(c.cohesion < LOW_COHESION_THRESHOLD).toBe(true);
  });

  it("cohesion = 0.85 is not low-cohesion", () => {
    const c: GraphCommunity = { id: 1, size: 50, cohesion: 0.85 };
    expect(c.cohesion < LOW_COHESION_THRESHOLD).toBe(false);
  });
});
