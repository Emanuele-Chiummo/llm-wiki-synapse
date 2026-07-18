/**
 * useInsightCount.ts — Custom hook: lazily compute the insights badge count at idle time.
 *
 * Move-only extraction from GraphViewer.tsx (FE-ARCH-1, 2.1 pass-2).
 * No behavior change. All logic, comments, and invariant annotations are preserved.
 *
 * I3 / AC-F4-6: computeGraphInsights scans the whole graph (surprising connections +
 * knowledge gaps) — a >50ms main-thread task on large graphs. It MUST NOT run on the
 * load/render critical path. This hook defers computation via requestIdleCallback
 * (or setTimeout as a fallback) so the initial graph render stays long-task-free.
 */

import { useEffect, useState } from "react";
import type { GraphCommunity, GraphEdge, GraphNode } from "../../api/types";
import { computeGraphInsights } from "./graphInsights";

/**
 * Returns the current graph insights count, computed lazily at idle time.
 * Returns 0 until the first idle-time computation completes.
 *
 * @param showInsightsPanel — defer computation until the panel is opened (AC-F4-6)
 * @param nodes / edges / communities — current graph data from the store
 */
export function useInsightCount(
  showInsightsPanel: boolean,
  nodes: GraphNode[],
  edges: GraphEdge[],
  communities: GraphCommunity[],
): number {
  const [insightCount, setInsightCount] = useState(0);

  useEffect(() => {
    if (!showInsightsPanel || nodes.length === 0) {
      return;
    }
    let cancelled = false;
    const compute = () => {
      if (!cancelled) setInsightCount(computeGraphInsights(nodes, edges, communities).total);
    };
    const w = window as typeof window & {
      requestIdleCallback?: (cb: () => void, opts?: { timeout: number }) => number;
      cancelIdleCallback?: (h: number) => void;
    };
    let handle: number;
    if (typeof w.requestIdleCallback === "function") {
      handle = w.requestIdleCallback(compute, { timeout: 1000 });
    } else {
      handle = window.setTimeout(compute, 300);
    }
    return () => {
      cancelled = true;
      if (typeof w.cancelIdleCallback === "function") w.cancelIdleCallback(handle);
      else window.clearTimeout(handle);
    };
  }, [showInsightsPanel, nodes, edges, communities]);

  return insightCount;
}
