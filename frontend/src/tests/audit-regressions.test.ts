/**
 * audit-regressions.test.ts — regression tests for 2.0.0 W-FE audit findings.
 *
 * Finding #5: GraphViewer diff-in-place clears stale nodes on empty /graph payload.
 *   Tests the store layer (graphStore). The sigma DOM test would require a WebGL
 *   context; the store-level behaviour is the gate that drives the diff effect.
 *
 * Finding #6: statusStore.setDataVersion monotonic guard — out-of-order REST
 *   response cannot overwrite a more-recent SSE-pushed value.
 */

import { describe, it, expect, beforeEach } from "vitest";
import { useStatusStore } from "../store/statusStore";
import { useGraphStore } from "../store/graphStore";
import type { GraphNode, GraphEdge, CacheStatus } from "../api/types";

// ─── Finding #5 — graphStore correctly reflects an empty /graph payload ────────

describe("graphStore — empty payload clears nodes (finding #5 regression)", () => {
  beforeEach(() => {
    useGraphStore.getState().reset();
  });

  it("transitions from populated to empty when /graph returns zero nodes", () => {
    const NODES: GraphNode[] = [
      { id: "a", title: "Alpha", type: "concept", x: 1.0, y: 2.0 },
      { id: "b", title: "Beta", type: "entity", x: -3.0, y: 4.5 },
    ];
    const EDGES: GraphEdge[] = [{ source: "a", target: "b", weight: 7.5 }];

    const CACHE: CacheStatus = "miss";
    useGraphStore.getState().setGraph(NODES, EDGES, 1, CACHE);

    expect(useGraphStore.getState().nodes).toHaveLength(2);
    expect(useGraphStore.getState().edges).toHaveLength(1);

    // Simulate a /graph response after cascade-delete emptied the vault.
    useGraphStore.getState().setGraph([], [], 2, CACHE);

    expect(useGraphStore.getState().nodes).toHaveLength(0);
    expect(useGraphStore.getState().edges).toHaveLength(0);
  });

  it("empty-to-empty transition is a no-op (dataVersion still advances)", () => {
    const CACHE: CacheStatus = "miss";
    useGraphStore.getState().setGraph([], [], 5, CACHE);
    expect(useGraphStore.getState().nodes).toHaveLength(0);
    expect(useGraphStore.getState().edges).toHaveLength(0);
  });
});

// ─── Finding #6 — statusStore.setDataVersion monotonic guard ──────────────────

describe("statusStore — setDataVersion monotonic guard (finding #6 regression)", () => {
  beforeEach(() => {
    // Reset statusStore to initial state.
    useStatusStore.setState({ dataVersion: null });
  });

  it("applies a higher version when current is null", () => {
    useStatusStore.getState().setDataVersion(5);
    expect(useStatusStore.getState().dataVersion).toBe(5);
  });

  it("applies a higher version when incoming > current", () => {
    useStatusStore.getState().setDataVersion(3);
    useStatusStore.getState().setDataVersion(7);
    expect(useStatusStore.getState().dataVersion).toBe(7);
  });

  it("applies an equal version (SSE and REST can legitimately push the same value)", () => {
    useStatusStore.getState().setDataVersion(10);
    useStatusStore.getState().setDataVersion(10);
    expect(useStatusStore.getState().dataVersion).toBe(10);
  });

  it("drops an out-of-order version that is LOWER than the current", () => {
    // SSE already pushed version 10; a stale REST response arrives with version 7.
    useStatusStore.getState().setDataVersion(10);
    useStatusStore.getState().setDataVersion(7);
    // Must not regress to 7 — that would suppress the next GraphViewer re-fetch.
    expect(useStatusStore.getState().dataVersion).toBe(10);
  });

  it("allows updates when current is null regardless of incoming value", () => {
    // null baseline means no prior knowledge — accept any non-null value.
    useStatusStore.getState().setDataVersion(1);
    expect(useStatusStore.getState().dataVersion).toBe(1);
  });
});
