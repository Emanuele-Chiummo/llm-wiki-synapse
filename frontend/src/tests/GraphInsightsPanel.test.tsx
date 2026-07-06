/**
 * GraphInsightsPanel.test.tsx — component tests for GraphInsightsPanel (F4, G-P1-5).
 *
 * Coverage:
 *   A. Renders insight rows when store has suitable graph data.
 *   B. Clicking a row calls setSelectedNodeId with the primaryNodeId.
 *   C. Dismiss button removes the row from the DOM.
 *   D. Deep-research button calls setActiveSection("deep-search").
 *   E. Empty state when no insights can be derived.
 *   F. Panel hidden (not in DOM) when graph has no nodes.
 *   G. Collapse/expand toggle hides and shows the body.
 *
 * Pattern: seed the Zustand store via useGraphStore.getState().setGraph(...)
 * then reset in beforeEach — matches graphCommunity.test.ts convention.
 *
 * INVARIANT I3: panel uses selectors + useShallow; no store-wide subscriptions.
 * INVARIANT I2: community/degree values are set directly in fixtures (server-supplied).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { GraphNode, GraphEdge, GraphCommunity } from "../api/types";
import { useGraphStore } from "../store/graphStore";

// ─── Mock react-i18next ───────────────────────────────────────────────────────

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      const map: Record<string, string> = {
        "graph.insights.title": "Graph Insights",
        "graph.insights.sectionSurprising": "Surprising connections",
        "graph.insights.sectionGaps": "Knowledge gaps",
        "graph.insights.subKindIsolated": "isolated nodes",
        "graph.insights.subKindSparse": "sparse communities",
        "graph.insights.subKindBridge": "bridge nodes",
        "graph.insights.dismissAriaLabel": "Dismiss insight",
        "graph.insights.deepResearch": "Deep Research",
        "graph.insights.collapseAriaLabel": "Collapse insights panel",
        "graph.insights.expandAriaLabel": "Expand insights panel",
        "graph.insights.empty": "No insights found.",
        "graph.insights.surprisingRow": `${String(params?.["source"])} — ${String(params?.["target"])} (score ${String(params?.["score"])})`,
        "graph.insights.sparseRow": `Community ${String(params?.["id"])}: ${String(params?.["size"])} nodes, ${String(params?.["cohesion"])}% cohesion`,
        "graph.insights.bridgeRow": `${String(params?.["title"])} spans ${String(params?.["count"])} communities`,
      };
      return map[key] ?? key;
    },
  }),
}));

// ─── Import component after mocks ─────────────────────────────────────────────

import { GraphInsightsPanel } from "../components/graph/GraphInsightsPanel";

// ─── Fixtures ─────────────────────────────────────────────────────────────────

function makeNode(
  id: string,
  title: string,
  type: string,
  community: number,
  degree: number,
): GraphNode {
  return { id, title, type, x: 0, y: 0, community, degree };
}

/** Graph with one cross-community high-weight edge (produces a surprising insight). */
const NODES_SURPRISING: GraphNode[] = [
  makeNode("n1", "Alpha", "concept", 0, 5),
  makeNode("n2", "Beta", "entity", 1, 5),
];
const EDGES_SURPRISING: GraphEdge[] = [{ source: "n1", target: "n2", weight: 5 }];
const COMMUNITIES_EMPTY: GraphCommunity[] = [];

/** Graph with an isolated node (degree 0). */
const NODES_ISOLATED: GraphNode[] = [
  makeNode("orphan", "Orphan Page", "concept", 0, 0),
  makeNode("hub", "Hub", "concept", 1, 5),
];
const EDGES_ISOLATED: GraphEdge[] = [];

/** Graph that produces a gap-sparse community. */
const NODES_SPARSE: GraphNode[] = [
  makeNode("s1", "S1", "concept", 7, 2),
  makeNode("s2", "S2", "concept", 7, 2),
  makeNode("s3", "S3", "entity", 7, 2),
];
const COMMUNITIES_SPARSE: GraphCommunity[] = [{ id: 7, size: 3, cohesion: 0.05 }];

/** Graph with no insights (all nodes well-connected, same community, weight < 3). */
const NODES_NO_INSIGHTS: GraphNode[] = [
  makeNode("a", "A", "concept", 0, 5),
  makeNode("b", "B", "concept", 0, 5),
];
const EDGES_LOW_WEIGHT: GraphEdge[] = [{ source: "a", target: "b", weight: 1 }];

// ─── Tests ────────────────────────────────────────────────────────────────────

describe("GraphInsightsPanel", () => {
  beforeEach(() => {
    useGraphStore.getState().reset();
  });

  /** Helper: expand the collapsed panel so rows become visible. */
  function expandPanel() {
    const expandBtn = screen.getByLabelText("Expand insights panel");
    fireEvent.click(expandBtn);
  }

  // ── A. Renders insight rows ──────────────────────────────────────────────────

  it("A: renders insight rows for a graph with surprising connections", () => {
    useGraphStore
      .getState()
      .setGraph(NODES_SURPRISING, EDGES_SURPRISING, 1, "hit", COMMUNITIES_EMPTY);

    render(<GraphInsightsPanel />);
    expandPanel();
    const rows = screen.getAllByTestId("graph-insight-row");
    expect(rows.length).toBeGreaterThanOrEqual(1);
  });

  it("A2: renders isolated node in gap rows", () => {
    useGraphStore
      .getState()
      .setGraph(NODES_ISOLATED, EDGES_ISOLATED, 1, "hit", COMMUNITIES_EMPTY);

    render(<GraphInsightsPanel />);
    expandPanel();
    const rows = screen.getAllByTestId("graph-insight-row");
    expect(rows.length).toBeGreaterThanOrEqual(1);
    // The isolated node's title should appear somewhere in the panel
    expect(screen.getByText(/Orphan Page/)).toBeDefined();
  });

  // ── B. Click row calls setSelectedNodeId ─────────────────────────────────────

  it("B: clicking an insight row calls setSelectedNodeId with the primaryNodeId", () => {
    useGraphStore
      .getState()
      .setGraph(NODES_SURPRISING, EDGES_SURPRISING, 1, "hit", COMMUNITIES_EMPTY);

    const spy = vi.spyOn(useGraphStore.getState(), "setSelectedNodeId");

    render(<GraphInsightsPanel />);
    expandPanel();
    const rows = screen.getAllByTestId("graph-insight-row");
    fireEvent.click(rows[0]!);

    expect(spy).toHaveBeenCalledWith(expect.stringContaining("n1"));
    spy.mockRestore();
  });

  // ── C. Dismiss removes a row ──────────────────────────────────────────────────

  it("C: clicking dismiss removes the row from the DOM", () => {
    useGraphStore
      .getState()
      .setGraph(NODES_SURPRISING, EDGES_SURPRISING, 1, "hit", COMMUNITIES_EMPTY);

    render(<GraphInsightsPanel />);
    expandPanel();
    const rowsBefore = screen.getAllByTestId("graph-insight-row");
    const countBefore = rowsBefore.length;

    const dismissBtn = screen.getAllByTestId("graph-insight-dismiss")[0]!;
    fireEvent.click(dismissBtn);

    const rowsAfter = screen.queryAllByTestId("graph-insight-row");
    expect(rowsAfter.length).toBe(countBefore - 1);
  });

  // ── D. Deep-research button calls setActiveSection("deep-search") ──────────

  it("D: deep-research button calls setActiveSection with 'deep-search'", () => {
    useGraphStore
      .getState()
      .setGraph(NODES_ISOLATED, EDGES_ISOLATED, 1, "hit", COMMUNITIES_EMPTY);

    const spy = vi.spyOn(useGraphStore.getState(), "setActiveSection");

    render(<GraphInsightsPanel />);
    expandPanel();
    const drBtns = screen.getAllByTestId("graph-insight-deep-research");
    expect(drBtns.length).toBeGreaterThanOrEqual(1);
    fireEvent.click(drBtns[0]!);

    expect(spy).toHaveBeenCalledWith("deep-search");
    spy.mockRestore();
  });

  // ── E. Empty state when no insights ──────────────────────────────────────────

  it("E: renders empty state when graph produces no insights", () => {
    useGraphStore
      .getState()
      .setGraph(NODES_NO_INSIGHTS, EDGES_LOW_WEIGHT, 1, "hit", COMMUNITIES_EMPTY);

    render(<GraphInsightsPanel />);
    // Empty state is shown in the header when collapsed too — expand to confirm body
    expandPanel();
    expect(screen.getByTestId("graph-insights-empty")).toBeDefined();
    expect(screen.queryAllByTestId("graph-insight-row")).toHaveLength(0);
  });

  // ── F. Panel not rendered when graph is empty ─────────────────────────────

  it("F: panel not rendered when graph has no nodes", () => {
    useGraphStore.getState().setGraph([], [], 1, "hit", []);

    render(<GraphInsightsPanel />);
    expect(screen.queryByTestId("graph-insights-panel")).toBeNull();
  });

  // ── G. Collapse/expand toggle ─────────────────────────────────────────────

  it("G: panel starts collapsed by default; expand shows rows; collapse hides them again", () => {
    useGraphStore
      .getState()
      .setGraph(NODES_SURPRISING, EDGES_SURPRISING, 1, "hit", COMMUNITIES_EMPTY);

    render(<GraphInsightsPanel />);

    // Collapsed by default — rows not visible
    expect(screen.queryAllByTestId("graph-insight-row")).toHaveLength(0);

    // Expand
    const expandBtn = screen.getByLabelText("Expand insights panel");
    fireEvent.click(expandBtn);
    expect(screen.getAllByTestId("graph-insight-row").length).toBeGreaterThan(0);

    // Collapse again
    const collapseBtn = screen.getByLabelText("Collapse insights panel");
    fireEvent.click(collapseBtn);
    expect(screen.queryAllByTestId("graph-insight-row")).toHaveLength(0);
  });

  // ── H. Sparse community shows rows ───────────────────────────────────────

  it("H: sparse community insight rendered as gap row", () => {
    useGraphStore
      .getState()
      .setGraph(NODES_SPARSE, [], 1, "hit", COMMUNITIES_SPARSE);

    render(<GraphInsightsPanel />);
    expandPanel();
    const rows = screen.getAllByTestId("graph-insight-row");
    expect(rows.length).toBeGreaterThanOrEqual(1);
    // Community text should reference community 7
    expect(screen.getByText(/Community 7/)).toBeDefined();
  });
});
