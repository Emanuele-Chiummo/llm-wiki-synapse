/**
 * graphDomainMode.test.ts — "domain" color mode (thematic areas, ADR-0054 F18).
 *
 *   A. ColorMode accepts "domain".
 *   B. buildSigmaGraph colors nodes with colorForDomain(node.domain) in "domain" mode,
 *      and leaves "type" / "community" modes unchanged.
 *   C. computeDomainLegendRows — one row per domain sorted by count desc, untagged bucket.
 *
 * INVARIANT I2: domain values are ALWAYS read from the server response; no client-side
 * domain assignment runs in any test.
 */

import { describe, it, expect } from "vitest";
import { colorForCommunity, colorForDomain, DOMAIN_UNTAGGED_COLOR } from "../components/graphPalette";
import type { ColorMode } from "../components/graphPalette";
import { buildSigmaGraph } from "../components/graph/buildSigmaGraph";
import { colorForType } from "../components/graph/graphViewerShared";
import { computeDomainLegendRows } from "../components/graphCommunityUtils";
import type { GraphNode, GraphEdge } from "../api/types";

const nodes: GraphNode[] = [
  { id: "n1", title: "A", type: "concept", x: 0, y: 0, community: 0, domain: "ServiceNow" },
  { id: "n2", title: "B", type: "concept", x: 4, y: 4, community: 0, domain: "ServiceNow" },
  { id: "n3", title: "C", type: "entity", x: 10, y: 20, community: 1, domain: "SAM-Licensing" },
  { id: "n4", title: "D", type: "source", x: 50, y: 50, community: -1, domain: null },
  { id: "n5", title: "E", type: "source", x: 60, y: 60, community: -1 },
];
const edges: GraphEdge[] = [{ source: "n1", target: "n2", weight: 1 } as GraphEdge];

describe("ColorMode — accepts the domain discriminant", () => {
  it("is assignable", () => {
    const mode: ColorMode = "domain";
    expect(mode).toBe("domain");
  });
});

describe("buildSigmaGraph — domain color mode (I2: server domain only)", () => {
  it("colors each node with colorForDomain(node.domain) in domain mode", () => {
    const { sigmaGraph } = buildSigmaGraph(nodes, edges, "domain");
    expect(sigmaGraph.getNodeAttribute("n1", "color")).toBe(colorForDomain("ServiceNow"));
    expect(sigmaGraph.getNodeAttribute("n2", "color")).toBe(colorForDomain("ServiceNow"));
    expect(sigmaGraph.getNodeAttribute("n3", "color")).toBe(colorForDomain("SAM-Licensing"));
    // untagged (null or absent) → neutral gray
    expect(sigmaGraph.getNodeAttribute("n4", "color")).toBe(DOMAIN_UNTAGGED_COLOR);
    expect(sigmaGraph.getNodeAttribute("n5", "color")).toBe(DOMAIN_UNTAGGED_COLOR);
  });

  it("same domain → same color; different domains → different colors", () => {
    const { sigmaGraph } = buildSigmaGraph(nodes, edges, "domain");
    expect(sigmaGraph.getNodeAttribute("n1", "color")).toBe(sigmaGraph.getNodeAttribute("n2", "color"));
    expect(sigmaGraph.getNodeAttribute("n1", "color")).not.toBe(
      sigmaGraph.getNodeAttribute("n3", "color"),
    );
  });

  it("leaves community and type modes unchanged", () => {
    const byCommunity = buildSigmaGraph(nodes, edges, "community").sigmaGraph;
    expect(byCommunity.getNodeAttribute("n1", "color")).toBe(colorForCommunity(0));
    expect(byCommunity.getNodeAttribute("n4", "color")).toBe(colorForCommunity(-1));
    const byType = buildSigmaGraph(nodes, edges, "type").sigmaGraph;
    expect(byType.getNodeAttribute("n1", "color")).toBe(colorForType("concept"));
    expect(byType.getNodeAttribute("n3", "color")).toBe(colorForType("entity"));
  });

  it("still stores nodeDomain for reducers in every mode", () => {
    for (const mode of ["type", "community", "domain"] as ColorMode[]) {
      const { sigmaGraph } = buildSigmaGraph(nodes, edges, mode);
      expect(sigmaGraph.getNodeAttribute("n3", "nodeDomain")).toBe("SAM-Licensing");
      expect(sigmaGraph.getNodeAttribute("n5", "nodeDomain")).toBeNull();
    }
  });
});

describe("computeDomainLegendRows — one row per domain, untagged bucket last (I3 pure)", () => {
  it("counts nodes per domain, sorted by count desc", () => {
    const rows = computeDomainLegendRows(nodes);
    expect(rows.named).toEqual([
      { domain: "ServiceNow", count: 2 },
      { domain: "SAM-Licensing", count: 1 },
    ]);
  });

  it("groups null, absent and blank domains into untaggedCount", () => {
    const rows = computeDomainLegendRows([
      ...nodes,
      { id: "n6", title: "F", type: "concept", x: 0, y: 0, community: -1, domain: "   " },
    ]);
    expect(rows.untaggedCount).toBe(3);
  });

  it("breaks count ties by name for a stable legend", () => {
    const rows = computeDomainLegendRows([
      { id: "a", title: "a", type: "concept", x: 0, y: 0, community: 0, domain: "Zeta" },
      { id: "b", title: "b", type: "concept", x: 0, y: 0, community: 0, domain: "Alfa" },
    ]);
    expect(rows.named.map((r) => r.domain)).toEqual(["Alfa", "Zeta"]);
  });

  it("returns empty rows for an empty graph", () => {
    expect(computeDomainLegendRows([])).toEqual({ named: [], untaggedCount: 0 });
  });
});
