/**
 * GraphViewerLiveDiff.test.tsx — FE-RT-1 (1.9.3 W2): verifies the sigma mount effect
 * diffs incoming graph data into the EXISTING sigma instance instead of killing and
 * rebuilding it on every background /graph refetch.
 *
 * Covers:
 *  (a) a data refresh with the SAME colorMode/theme does NOT call sigma.kill()
 *      (no WebGL context churn, no event-handler re-registration, no camera reset).
 *  (b) a colorMode change (Type ↔ Community) DOES call sigma.kill() and mounts a
 *      fresh instance (new node colors need a new render context — I2-safe: still
 *      only reads server-provided x/y, never computes layout).
 *  (c) camera.animatedReset() is called on the true initial mount ONLY — never on
 *      a background data diff, and not on a colorMode-driven rebuild either.
 *
 * INVARIANT I2: the "sigma" package is mocked (jsdom has no WebGL2) but the diff
 *   logic under test runs against REAL graphology graphs (via the real
 *   buildGraphologyGraph) — node/edge x/y always come from the GraphNode/GraphEdge
 *   fixtures below (never computed here), matching production behaviour exactly.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, cleanup, act, fireEvent } from "@testing-library/react";
import { useGraphStore } from "../store/graphStore";
import { useAppStore } from "../store/appStore";
import { useStatusStore } from "../store/statusStore";
import type { GraphNode, GraphEdge } from "../api/types";

// ─── i18n mock (pass-through keys, matches existing test pattern) ────────────
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

// ─── api/graphClient mock — no real network calls from jsdom ────────────────
vi.mock("../api/graphClient", () => ({
  fetchGraph: vi.fn(() =>
    Promise.resolve({ data: { nodes: [], edges: [], data_version: 1 }, cacheStatus: "hit" }),
  ),
  fetchPageDetail: vi.fn(() => Promise.reject(new Error("not used in this test"))),
  patchNodePosition: vi.fn(() => Promise.resolve()),
  recomputeGraph: vi.fn(() => Promise.resolve({ reconnected: 0 })),
  fetchCommunityDetail: vi.fn(() => Promise.reject(new Error("not used in this test"))),
  fetchEdgeDetail: vi.fn(() => Promise.reject(new Error("not used in this test"))),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

// ─── sigma mock — tracks kill() / animatedReset() / refresh() without WebGL ──
// getGraph() returns the REAL graphology graph passed at construction so the
// diff effect's mergeNodeAttributes/addNode/dropNode calls exercise real logic.

const killSpy = vi.fn();
const animatedResetSpy = vi.fn();
const refreshSpy = vi.fn();
let sigmaInstanceCount = 0;
let lastConstructedGraph: import("graphology").default | undefined;

vi.mock("sigma", () => {
  class FakeSigma {
    private graph: import("graphology").default;
    constructor(graph: import("graphology").default) {
      this.graph = graph;
      lastConstructedGraph = graph;
      sigmaInstanceCount += 1;
    }
    kill() {
      killSpy();
    }
    refresh(opts?: unknown) {
      refreshSpy(opts);
    }
    scheduleRefresh() {}
    getGraph() {
      return this.graph;
    }
    getCamera() {
      return {
        animatedReset: (opts?: unknown) => animatedResetSpy(opts),
        animatedZoom: () => {},
        animatedUnzoom: () => {},
        animate: () => {},
      };
    }
    on() {
      return this;
    }
    off() {
      return this;
    }
    viewportToGraph(p: unknown) {
      return p;
    }
    graphToViewport(p: unknown) {
      return p;
    }
  }
  return { default: FakeSigma };
});

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const NODES_V1: GraphNode[] = [
  { id: "a", title: "Alpha", type: "concept", x: 0, y: 0, degree: 1 },
  { id: "b", title: "Beta", type: "entity", x: 1, y: 1, degree: 1 },
];
const EDGES_V1: GraphEdge[] = [{ source: "a", target: "b", weight: 5 }];

// V2 simulates a background /graph refetch mid-ingest: same "a"/"b" plus a NEW
// node "c" that just got created by the ingest loop (the "watch the wiki grow" case).
const NODES_V2: GraphNode[] = [
  ...NODES_V1,
  { id: "c", title: "Gamma", type: "concept", x: 2, y: 2, degree: 1 },
];
const EDGES_V2: GraphEdge[] = [...EDGES_V1, { source: "b", target: "c", weight: 3 }];

async function mountGraphViewer() {
  const { GraphViewer } = await import("../components/GraphViewer");
  return render(<GraphViewer />);
}

beforeEach(() => {
  useGraphStore.getState().reset();
  useAppStore.getState().reset();
  useStatusStore.getState().setDataVersion(null);
  killSpy.mockClear();
  animatedResetSpy.mockClear();
  refreshSpy.mockClear();
  sigmaInstanceCount = 0;
  lastConstructedGraph = undefined;

  // Seed the store BEFORE mount so the initial-fetch effect's cache-hit branch
  // skips the network round-trip entirely (store dataVersion === status dataVersion).
  useGraphStore.getState().setGraph(NODES_V1, EDGES_V1, 1, "hit");
  useStatusStore.getState().setDataVersion(1);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("FE-RT-1 — GraphViewer sigma live-diff (no kill+rebuild on background refresh)", () => {
  it("(initial mount) constructs exactly one sigma instance and resets the camera once", async () => {
    await act(async () => {
      await mountGraphViewer();
    });

    expect(sigmaInstanceCount).toBe(1);
    expect(killSpy).not.toHaveBeenCalled();
    expect(animatedResetSpy).toHaveBeenCalledTimes(1);
  });

  it("(a) a same-theme/same-colorMode data refresh does NOT call sigma.kill()", async () => {
    await act(async () => {
      await mountGraphViewer();
    });
    expect(sigmaInstanceCount).toBe(1);

    // Simulate the throttled background /graph refetch (RT-3) delivering a new
    // node — the exact scenario that used to kill+rebuild sigma every ~10s.
    await act(async () => {
      useGraphStore.getState().setGraph(NODES_V2, EDGES_V2, 2, "hit");
    });

    expect(killSpy).not.toHaveBeenCalled();
    expect(sigmaInstanceCount).toBe(1); // no new Sigma() constructed
    expect(refreshSpy).toHaveBeenCalled(); // but the live graph WAS repainted

    // The diff must have added the new node into the EXISTING graphology graph.
    expect(lastConstructedGraph?.hasNode("c")).toBe(true);
  });

  it("(b) a colorMode change (Type → Community) DOES call sigma.kill() and rebuilds", async () => {
    const { getByTestId } = await (async () => {
      let result!: ReturnType<typeof render>;
      await act(async () => {
        result = await mountGraphViewer();
      });
      return result;
    })();

    expect(sigmaInstanceCount).toBe(1);
    expect(killSpy).not.toHaveBeenCalled();

    await act(async () => {
      fireEvent.click(getByTestId("color-mode-community"));
    });

    expect(killSpy).toHaveBeenCalledTimes(1);
    expect(sigmaInstanceCount).toBe(2); // rebuilt with a fresh instance/context
  });

  it("(c) animatedReset() is NOT called again on a background data refresh", async () => {
    await act(async () => {
      await mountGraphViewer();
    });
    expect(animatedResetSpy).toHaveBeenCalledTimes(1); // initial mount only

    await act(async () => {
      useGraphStore.getState().setGraph(NODES_V2, EDGES_V2, 2, "hit");
    });

    // Still exactly 1 — the diff path never touches the camera.
    expect(animatedResetSpy).toHaveBeenCalledTimes(1);
  });

  it("(c) animatedReset() is NOT re-triggered by a colorMode-driven rebuild either", async () => {
    const { getByTestId } = await (async () => {
      let result!: ReturnType<typeof render>;
      await act(async () => {
        result = await mountGraphViewer();
      });
      return result;
    })();
    expect(animatedResetSpy).toHaveBeenCalledTimes(1);

    await act(async () => {
      fireEvent.click(getByTestId("color-mode-community"));
    });

    // Rebuild happened (kill+new instance — see test (b)) but the camera is only
    // reset on the true first mount, never on a subsequent rebuild.
    expect(animatedResetSpy).toHaveBeenCalledTimes(1);
  });
});
