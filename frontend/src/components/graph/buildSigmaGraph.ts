/**
 * buildSigmaGraph.ts — Pure function: GraphNode[]/GraphEdge[] → sigma-ready graphology Graph.
 *
 * Move-only extraction from GraphViewer.tsx (FE-ARCH-1, 2.1 pass-2).
 * No behavior change. Called by GraphViewer's thin useCallback wrapper.
 *
 * INVARIANT I2: x/y coordinates are copied DIRECTLY from the server-provided GraphNode
 * objects. This function MUST NOT invoke any layout algorithm or mutate positions.
 */

import type { Attributes } from "graphology-types";
import type { ColorMode } from "../graphPalette";
import { colorForCommunity } from "../graphPalette";
import type { GraphNode, GraphEdge } from "../../api/types";
import { buildGraphologyGraph } from "../../api/graphTransform";
import { colorForType } from "./graphViewerShared";

/**
 * Build a sigma-ready graphology graph from raw node/edge arrays.
 *
 * @returns rawGraph  — the typed SynapseGraph (GraphNode attrs, GraphEdge attrs)
 * @returns sigmaGraph — a plain-Attributes graph suitable for `new Sigma(sigmaGraph, …)`
 *
 * I2: Only copies server-provided x/y — no layout call, ever.
 */
export function buildSigmaGraph(
  srcNodes: GraphNode[],
  srcEdges: GraphEdge[],
  mode: ColorMode,
): {
  rawGraph: ReturnType<typeof buildGraphologyGraph>;
  sigmaGraph: import("graphology").default;
} {
  const isDarkTheme = document.documentElement.getAttribute("data-theme") === "dark";
  const rawGraph = buildGraphologyGraph(srcNodes, srcEdges, isDarkTheme ? "dark" : "light");

  // Build a plain Attributes graphology graph for sigma.
  // Copy all node/edge attributes (including server x/y) into the sigma graph.
  const SigmaGraphCtor = rawGraph.constructor as new (opts: {
    multi: boolean;
    type: string;
  }) => import("graphology").default;
  const sigmaGraph = new SigmaGraphCtor({ multi: false, type: "undirected" });

  rawGraph.forEachNode((nodeKey, rawAttrs) => {
    // Cast to Attributes (Record<string,unknown>) so dynamic key access is type-safe.
    const attrs = rawAttrs as Attributes;
    const nodeType = attrs["type"] as string | null | undefined;
    // Community id — -1 when unassigned or from older servers (non-breaking, set in graphTransform)
    const nodeCommunity = (attrs["community"] as number | undefined) ?? -1;
    // Domain name — null when untagged or from older servers (non-breaking).
    // I2: value comes from the server (GraphNode.domain); never computed client-side.
    const nodeDomain = (attrs["domain"] as string | null | undefined) ?? null;
    // Color is determined by active color-mode (I2: all values come from server).
    // "community" mode colors by Louvain community id — one distinct color per cluster
    // from COMMUNITY_PALETTE (cycles for >12 communities; -1 = unassigned → gray).
    // "type" mode colors by page type (concept, entity, source, …).
    const nodeColor =
      mode === "community"
        ? colorForCommunity(nodeCommunity, isDarkTheme ? "dark" : "light")
        : colorForType(nodeType ?? null);
    sigmaGraph.addNode(nodeKey, {
      x: attrs["x"] as number,
      y: attrs["y"] as number,
      label: attrs["label"] as string,
      // GL2: pre-truncated hub label; nodeReducer swaps this in for hub nodes at rest
      hubLabel: (attrs["hubLabel"] as string | undefined) ?? (attrs["label"] as string),
      size: attrs["size"] as number,
      color: nodeColor,
      // Store degree for reducers
      degree: attrs["degree"] as number,
      // Store type for reducers
      nodeType: nodeType ?? null,
      // Store community for reducers / tooltip
      nodeCommunity,
      // Store domain for reducers (I2: from server)
      nodeDomain,
      // GL2: hub flag — nodeReducer uses this to force permanent truncated label on top-K hubs
      isHub: (attrs["forceLabel"] as boolean | undefined) ?? false,
    });
  });

  rawGraph.forEachEdge((_edgeKey, attrs, source, target) => {
    if (!sigmaGraph.hasEdge(source, target)) {
      sigmaGraph.addEdge(source, target, {
        weight: attrs["weight"] as number,
        color: attrs["color"] as string,
        size: attrs["size"] as number,
        // GL1: pass through normalizedWeight so edgeReducer can check it on hover
        normalizedWeight: attrs["normalizedWeight"] as number,
        // GL1: resting hidden flag (weak edges culled at rest, revealed on hover)
        hidden: attrs["hidden"] as boolean,
      });
    }
  });

  return { rawGraph, sigmaGraph };
}
