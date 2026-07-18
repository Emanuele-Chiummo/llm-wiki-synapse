/**
 * useGraphFilterSync.ts — Custom hook: sync filter store values into refs + trigger sigma refresh.
 *
 * Move-only extraction from GraphViewer.tsx (FE-ARCH-1, 2.1 pass-2).
 * No behavior change. All logic, comments, and invariant annotations are preserved.
 *
 * Creates and owns the filter refs that the sigma mount effect's nodeReducer/edgeReducer
 * close over. Because useRef returns the same stable object across renders, the closed-over
 * refs in the sigma mount effect always read the latest values mutated here — no re-render
 * triggered per filter change (I3), no layout invoked (I2).
 */

import { useEffect, useRef } from "react";
import type { MutableRefObject } from "react";
import type Sigma from "sigma";
import type { Attributes } from "graphology-types";
import type { GraphNode } from "../../api/types";

interface UseGraphFilterSyncParams {
  /** GR3: active node-type filter set */
  filterNodeTypes: Set<string>;
  /** GI-2: hide meta-type nodes (index/overview/log) */
  hideMetaTypes: boolean;
  /** GI-2: hide isolated nodes (degree 0) */
  hideIsolated: boolean;
  /** GI-2: minimum degree filter (null = no lower bound) */
  minLinks: number | null;
  /** GI-2: maximum degree filter (null = no upper bound) */
  maxLinks: number | null;
  /** GI-2: node size scale multiplier (1.0 = 100%) */
  nodeSizeScale: number;
  /** GI-2: coordinate spacing scale multiplier (1.0 = 100%) */
  spacingScale: number;
  /** Store nodes — needed by spacing-scale effect to build position lookup (I2: read-only) */
  nodes: GraphNode[];
  /** Sigma instance ref — used to call sigma.refresh() after each filter change */
  sigmaRef: MutableRefObject<Sigma<Attributes, Attributes, Attributes> | null>;
}

interface UseGraphFilterSyncResult {
  /** GR3 ref: nodeReducer reads this for type filtering */
  filterNodeTypesRef: MutableRefObject<Set<string>>;
  /** GI-2 refs: nodeReducer/edgeReducer read these for extended filters */
  hideMetaTypesRef: MutableRefObject<boolean>;
  hideIsolatedRef: MutableRefObject<boolean>;
  minLinksRef: MutableRefObject<number | null>;
  maxLinksRef: MutableRefObject<number | null>;
  nodeSizeScaleRef: MutableRefObject<number>;
}

/**
 * Manages all filter-related ref syncing and sigma.refresh() calls.
 *
 * Returns the stable refs so GraphViewer's sigma mount effect can close over them
 * in its nodeReducer and edgeReducer. The returned refs are the SAME objects across
 * renders (useRef identity) — mutations here are immediately visible to any active
 * sigma reducers without rebuilding sigma.
 */
export function useGraphFilterSync({
  filterNodeTypes,
  hideMetaTypes,
  hideIsolated,
  minLinks,
  maxLinks,
  nodeSizeScale,
  spacingScale,
  nodes,
  sigmaRef,
}: UseGraphFilterSyncParams): UseGraphFilterSyncResult {
  // GR3 (v1.3.14): ordering note — these refs must be declared BEFORE the effects
  // that capture them, to avoid a temporal-dead-zone (the refs' initial useRef(...)
  // reads these values from the parent). Live-preview caught this ordering bug.
  const filterNodeTypesRef = useRef<Set<string>>(filterNodeTypes);
  const hideMetaTypesRef = useRef<boolean>(hideMetaTypes);
  const hideIsolatedRef = useRef<boolean>(hideIsolated);
  const minLinksRef = useRef<number | null>(minLinks);
  const maxLinksRef = useRef<number | null>(maxLinks);
  const nodeSizeScaleRef = useRef<number>(nodeSizeScale);

  // ── GR3: sync filterNodeTypes ref and refresh sigma on filter change ──────
  // The ref lets the existing sigma reducers always see the latest filter value
  // without tearing down and rebuilding sigma on every toggle (I3: no heavy
  // work per frame; I2: no coords touched, only sigma's hidden flag is changed).
  useEffect(() => {
    filterNodeTypesRef.current = filterNodeTypes;
    // Trigger a visual refresh so sigma re-evaluates nodeReducer/edgeReducer
    // with the updated filter. skipIndexation: layout is not touched (I2).
    sigmaRef.current?.refresh({ skipIndexation: true });
  }, [filterNodeTypes, sigmaRef]);

  // ── GI-2: sync filter refs and trigger visual refresh when visibility filters change ──
  // I2-safe: only sets hidden flags in reducers; never touches node coordinates.
  // I3-safe: updates refs (not state) so no re-render is triggered; sigma.refresh once.
  useEffect(() => {
    hideMetaTypesRef.current = hideMetaTypes;
    hideIsolatedRef.current = hideIsolated;
    minLinksRef.current = minLinks;
    maxLinksRef.current = maxLinks;
    sigmaRef.current?.refresh({ skipIndexation: true });
  }, [hideMetaTypes, hideIsolated, minLinks, maxLinks, sigmaRef]);

  // ── GI-2: node size scale — visual multiplier applied in nodeReducer via ref ──────
  useEffect(() => {
    nodeSizeScaleRef.current = nodeSizeScale;
    sigmaRef.current?.refresh({ skipIndexation: true });
  }, [nodeSizeScale, sigmaRef]);

  // ── GI-2: spacing scale — translate sigma node positions around the centroid ───────
  // Uses original `nodes` from store as source of truth for positions (I2: precomputed
  // by server; pure arithmetic scale around centroid — no FA2, no force iteration).
  // skipIndexation:false so sigma re-indexes the rescaled positions into camera space.
  useEffect(() => {
    const sigma = sigmaRef.current;
    if (!sigma || nodes.length === 0) return;
    const sigmaGraph = sigma.getGraph();
    if (sigmaGraph.order === 0) return;

    // Build O(n) position lookup from server-side positions (never mutated by client)
    let sumX = 0;
    let sumY = 0;
    const origPos = new Map<string, { x: number; y: number }>();
    for (const n of nodes) {
      origPos.set(n.id, { x: n.x, y: n.y });
      sumX += n.x;
      sumY += n.y;
    }
    const cx = sumX / nodes.length;
    const cy = sumY / nodes.length;

    // Scale each node position around the centroid (pure arithmetic — I2)
    sigmaGraph.forEachNode((nodeKey) => {
      const orig = origPos.get(nodeKey);
      if (!orig) return;
      sigmaGraph.setNodeAttribute(nodeKey, "x", cx + (orig.x - cx) * spacingScale);
      sigmaGraph.setNodeAttribute(nodeKey, "y", cy + (orig.y - cy) * spacingScale);
    });

    sigma.refresh({ skipIndexation: false });
  }, [spacingScale, nodes, sigmaRef]);

  return {
    filterNodeTypesRef,
    hideMetaTypesRef,
    hideIsolatedRef,
    minLinksRef,
    maxLinksRef,
    nodeSizeScaleRef,
  };
}
