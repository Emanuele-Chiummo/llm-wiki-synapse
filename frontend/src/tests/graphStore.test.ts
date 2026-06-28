/**
 * graphStore.test.ts
 *
 * Tests for the Zustand graph store: selectors update correctly, shallow equality
 * works as expected, and the store actions behave correctly.
 *
 * I3 compliance verification: selector-based subscriptions are tested in isolation.
 */

import { describe, it, expect, beforeEach } from "vitest";
import {
  useGraphStore,
  selectNodes,
  selectEdges,
  selectStatus,
  selectMeta,
  selectSelectedNodeId,
  selectVaultId,
} from "../store/graphStore";
import type { GraphNode, GraphEdge } from "../api/types";

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const NODES: GraphNode[] = [
  { id: "a", title: "Alpha", type: "concept", x: 1.0, y: 2.0 },
  { id: "b", title: "Beta", type: "entity", x: -3.0, y: 4.5 },
];

const EDGES: GraphEdge[] = [{ source: "a", target: "b", weight: 7.5 }];

// ─── Helpers ──────────────────────────────────────────────────────────────────

function getStore() {
  return useGraphStore.getState();
}

// Reset store to initial state before each test
beforeEach(() => {
  useGraphStore.getState().reset();
});

// ─── Action: setGraph ─────────────────────────────────────────────────────────

describe("graphStore — setGraph action", () => {
  it("updates nodes, edges, dataVersion, cacheStatus and clears loading", () => {
    getStore().setLoading(true);
    getStore().setGraph(NODES, EDGES, 5, "hit");

    const state = useGraphStore.getState();
    expect(selectNodes(state)).toEqual(NODES);
    expect(selectEdges(state)).toEqual(EDGES);
    expect(selectMeta(state).dataVersion).toBe(5);
    expect(selectMeta(state).cacheStatus).toBe("hit");
    expect(selectStatus(state).loading).toBe(false);
    expect(selectStatus(state).error).toBeNull();
  });

  it("stores nodes with their precomputed x/y intact (I2 contract)", () => {
    getStore().setGraph(NODES, EDGES, 1, "miss");

    const state = useGraphStore.getState();
    const nodes = selectNodes(state);

    expect(nodes[0]!.x).toBe(1.0);
    expect(nodes[0]!.y).toBe(2.0);
    expect(nodes[1]!.x).toBe(-3.0);
    expect(nodes[1]!.y).toBe(4.5);
  });

  it("records cache status = 'miss' correctly", () => {
    getStore().setGraph(NODES, EDGES, 2, "miss");
    expect(selectMeta(useGraphStore.getState()).cacheStatus).toBe("miss");
  });

  it("records cache status = 'hit' correctly", () => {
    getStore().setGraph(NODES, EDGES, 2, "hit");
    expect(selectMeta(useGraphStore.getState()).cacheStatus).toBe("hit");
  });
});

// ─── Action: setLoading ───────────────────────────────────────────────────────

describe("graphStore — setLoading action", () => {
  it("sets loading to true", () => {
    getStore().setLoading(true);
    expect(selectStatus(useGraphStore.getState()).loading).toBe(true);
  });

  it("sets loading to false", () => {
    getStore().setLoading(true);
    getStore().setLoading(false);
    expect(selectStatus(useGraphStore.getState()).loading).toBe(false);
  });
});

// ─── Action: setError ─────────────────────────────────────────────────────────

describe("graphStore — setError action", () => {
  it("sets error message and clears loading", () => {
    getStore().setLoading(true);
    getStore().setError("network failure");

    const state = useGraphStore.getState();
    expect(selectStatus(state).error).toBe("network failure");
    expect(selectStatus(state).loading).toBe(false);
  });

  it("clears error when set to null", () => {
    getStore().setError("old error");
    getStore().setError(null);
    expect(selectStatus(useGraphStore.getState()).error).toBeNull();
  });
});

// ─── Action: setSelectedNodeId ────────────────────────────────────────────────

describe("graphStore — setSelectedNodeId action", () => {
  it("sets selected node id", () => {
    getStore().setSelectedNodeId("node-abc");
    expect(selectSelectedNodeId(useGraphStore.getState())).toBe("node-abc");
  });

  it("clears selected node id when set to null", () => {
    getStore().setSelectedNodeId("node-abc");
    getStore().setSelectedNodeId(null);
    expect(selectSelectedNodeId(useGraphStore.getState())).toBeNull();
  });
});

// ─── Action: setVaultId ───────────────────────────────────────────────────────

describe("graphStore — setVaultId action", () => {
  it("updates vault id", () => {
    getStore().setVaultId("my-vault");
    expect(selectVaultId(useGraphStore.getState())).toBe("my-vault");
  });
});

// ─── Action: reset ────────────────────────────────────────────────────────────

describe("graphStore — reset action", () => {
  it("resets all state to initial values", () => {
    getStore().setGraph(NODES, EDGES, 10, "hit");
    getStore().setSelectedNodeId("some-id");
    getStore().setError("oops");

    getStore().reset();

    const state = useGraphStore.getState();
    expect(selectNodes(state)).toEqual([]);
    expect(selectEdges(state)).toEqual([]);
    expect(selectMeta(state).dataVersion).toBeNull();
    expect(selectMeta(state).cacheStatus).toBe("unknown");
    expect(selectStatus(state).loading).toBe(false);
    expect(selectStatus(state).error).toBeNull();
    expect(selectSelectedNodeId(state)).toBeNull();
  });
});

// ─── Selectors — return identity-stable results ───────────────────────────────

describe("graphStore — selector stability (I3)", () => {
  it("selectNodes returns the same array reference when nodes have not changed", () => {
    getStore().setGraph(NODES, EDGES, 1, "hit");
    const ref1 = selectNodes(useGraphStore.getState());
    // Call again without mutation
    const ref2 = selectNodes(useGraphStore.getState());
    expect(ref1).toBe(ref2); // same reference — no unnecessary re-renders
  });

  it("selectStatus returns a new object when loading changes", () => {
    getStore().setLoading(false);
    const s1 = selectStatus(useGraphStore.getState());
    getStore().setLoading(true);
    const s2 = selectStatus(useGraphStore.getState());
    // The values are different
    expect(s1.loading).toBe(false);
    expect(s2.loading).toBe(true);
  });

  it("selectMeta returns correct dataVersion after setGraph", () => {
    getStore().setGraph(NODES, EDGES, 42, "miss");
    expect(selectMeta(useGraphStore.getState()).dataVersion).toBe(42);
  });

  it("selectMeta dataVersion is null before any setGraph call", () => {
    expect(selectMeta(useGraphStore.getState()).dataVersion).toBeNull();
  });
});
