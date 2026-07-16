/**
 * graphStore.ts — Zustand store for Synapse graph DATA + visual filters (F1 / ADR-0017).
 *
 * FE-ARCH-6: navigation/vault/selection state (activeSection, vaultId,
 * selectedNodeId/selectedSource, activeTab, treeCollapsed, showInsightsPanel)
 * was extracted into `appStore.ts` — this store now owns ONLY graph
 * data (nodes/edges/communities/dataVersion) and visual filters. See
 * appStore.ts for the navigation slice and its selectors.
 *
 * INVARIANT I3 compliance:
 *   - All components subscribe via SELECTOR FUNCTIONS, never the whole store.
 *   - Collections use shallow equality to prevent re-renders on unrelated changes.
 *   - Typed selectors are exported below — components import those, not raw state.
 *
 * See ADR-0015 §3, ADR-0017 §4.
 */

import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import type { CacheStatus, GraphCommunity, GraphEdge, GraphNode } from "../api/types";

// ─── State shape ──────────────────────────────────────────────────────────────

export interface GraphState {
  // Data
  nodes: GraphNode[];
  edges: GraphEdge[];
  /**
   * Community summary list from GET /graph (server-computed Louvain, v0.6+).
   * Empty array when the server doesn't return communities (old server / no graph data).
   * INVARIANT I2: client NEVER computes communities; only stores what the server returns.
   */
  communities: GraphCommunity[];
  dataVersion: number | null;
  cacheStatus: CacheStatus;

  /**
   * GR1: Total node/edge counts from GET /graph (all live vault pages/links,
   * pre client-filter). Used by GraphHeader stats chips.
   * null = server doesn't expose these fields yet (older backend).
   */
  totalNodes: number | null;
  totalEdges: number | null;

  /**
   * GR3: Active node-type filter. When non-empty, only nodes whose type is in
   * this set are shown; all others are hidden via sigma reducers.
   * Empty set = show all types (no filter active).
   * I2-safe: visibility only — never touches x/y or triggers re-layout.
   */
  filterNodeTypes: Set<string>;

  // ── GI-2 (v1.3.14) visual filter & display-tuning fields ─────────────────
  /** Hide meta-type nodes (index, overview, log) from sigma rendering. I2-safe: visibility only. */
  hideMetaTypes: boolean;
  /** Hide isolated nodes (degree 0) from sigma rendering. I2-safe: visibility only. */
  hideIsolated: boolean;
  /** Minimum degree a node must have to be shown (null = no lower bound). */
  minLinks: number | null;
  /** Maximum degree a node must have to be shown (null = no upper bound). */
  maxLinks: number | null;
  /**
   * Node size scale multiplier (range 0–2, default 1.0 = 100%).
   * Applied in sigma nodeReducer: size *= nodeSizeScale. No layout re-run (I2).
   */
  nodeSizeScale: number;
  /**
   * Coordinate spacing scale multiplier (range 0–2, default 1.0 = 100%).
   * Applied by scaling each node's x/y around the graph centroid (server-provided origin).
   * I2-safe: pure arithmetic on already-server-computed positions; no FA2 or layout invoked.
   */
  spacingScale: number;

  // Loading / error
  loading: boolean;
  error: string | null;
}

// ─── Actions ──────────────────────────────────────────────────────────────────

export interface GraphActions {
  setGraph: (
    nodes: GraphNode[],
    edges: GraphEdge[],
    dataVersion: number,
    cacheStatus: CacheStatus,
    communities?: GraphCommunity[],
    totalNodes?: number | null,
    totalEdges?: number | null,
  ) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  reset: () => void;
  /**
   * GR3: Toggle a node type in/out of the active filter set.
   * Empty set = all visible. I2-safe: never touches coords.
   */
  toggleFilterNodeType: (nodeType: string) => void;
  /** GR4: Clear all active type filters. */
  clearFilterNodeTypes: () => void;
  // ── GI-2 (v1.3.14) filter setters ────────────────────────────────────────
  setHideMetaTypes(v: boolean): void;
  setHideIsolated(v: boolean): void;
  setMinLinks(v: number | null): void;
  setMaxLinks(v: number | null): void;
  setNodeSizeScale(v: number): void;
  setSpacingScale(v: number): void;
  /**
   * Clear ALL visual filter state: filterNodeTypes + all GI-2 fields.
   * Called by the toolbar Reset button (in addition to camera fit).
   */
  clearAllGraphFilters(): void;
}

export type GraphStore = GraphState & GraphActions;

// ─── Initial state ───────────────────────────────────────────────────────────

const INITIAL_STATE: GraphState = {
  nodes: [],
  edges: [],
  communities: [],
  dataVersion: null,
  cacheStatus: "unknown",
  totalNodes: null,
  totalEdges: null,
  filterNodeTypes: new Set<string>(),
  // GI-2 (v1.3.14) visual filter defaults
  hideMetaTypes: false,
  hideIsolated: false,
  minLinks: null,
  maxLinks: null,
  nodeSizeScale: 1.0,
  spacingScale: 1.0,
  loading: false,
  error: null,
};

// ─── Store ────────────────────────────────────────────────────────────────────

/**
 * useGraphStore — always call with a selector.
 *
 * CORRECT:   const nodes = useGraphStore(selectNodes);
 * FORBIDDEN: const store = useGraphStore();  // subscribes to everything → I3 violation
 */
export const useGraphStore = create<GraphStore>((set) => ({
  ...INITIAL_STATE,

  setGraph: (
    nodes,
    edges,
    dataVersion,
    cacheStatus,
    communities = [],
    totalNodes = null,
    totalEdges = null,
  ) =>
    set({
      nodes,
      edges,
      communities,
      dataVersion,
      cacheStatus,
      totalNodes,
      totalEdges,
      loading: false,
      error: null,
    }),

  setLoading: (loading) => set({ loading }),

  setError: (error) => set({ error, loading: false }),

  reset: () => set(INITIAL_STATE),

  toggleFilterNodeType: (nodeType) =>
    set((s) => {
      const next = new Set(s.filterNodeTypes);
      if (next.has(nodeType)) {
        next.delete(nodeType);
      } else {
        next.add(nodeType);
      }
      return { filterNodeTypes: next };
    }),

  clearFilterNodeTypes: () => set({ filterNodeTypes: new Set<string>() }),

  // ── GI-2 (v1.3.14) filter actions ────────────────────────────────────────
  setHideMetaTypes: (hideMetaTypes) => set({ hideMetaTypes }),
  setHideIsolated: (hideIsolated) => set({ hideIsolated }),
  setMinLinks: (minLinks) => set({ minLinks }),
  setMaxLinks: (maxLinks) => set({ maxLinks }),
  setNodeSizeScale: (nodeSizeScale) => set({ nodeSizeScale }),
  setSpacingScale: (spacingScale) => set({ spacingScale }),
  clearAllGraphFilters: () =>
    set({
      filterNodeTypes: new Set<string>(),
      hideMetaTypes: false,
      hideIsolated: false,
      minLinks: null,
      maxLinks: null,
      nodeSizeScale: 1.0,
      spacingScale: 1.0,
    }),
}));

// ─── Typed selectors (I3) ─────────────────────────────────────────────────────
//
// Components import these selector functions and pass them to useGraphStore().
// Shallow equality is applied at the right granularity:
//   - scalar selectors use Object.is (Zustand default)
//   - collection/object selectors must use useShallow in the calling hook

/** Select the nodes array. */
export function selectNodes(s: GraphStore): GraphNode[] {
  return s.nodes;
}

/** Select the edges array. */
export function selectEdges(s: GraphStore): GraphEdge[] {
  return s.edges;
}

/** Select the communities array (server-computed Louvain, v0.6+). Use with useShallow. */
export function selectCommunities(s: GraphStore): GraphCommunity[] {
  return s.communities;
}

/** Select loading + error status as a shallow-compared object. */
export function selectStatus(s: GraphStore): { loading: boolean; error: string | null } {
  return { loading: s.loading, error: s.error };
}

/** Select cache metadata as a shallow-compared object. */
export function selectMeta(s: GraphStore): {
  dataVersion: number | null;
  cacheStatus: CacheStatus;
} {
  return { dataVersion: s.dataVersion, cacheStatus: s.cacheStatus };
}

/** GR1: Select totalNodes from the backend response (null = old server). */
export function selectTotalNodes(s: GraphStore): number | null {
  return s.totalNodes;
}

/** GR1: Select totalEdges from the backend response (null = old server). */
export function selectTotalEdges(s: GraphStore): number | null {
  return s.totalEdges;
}

/** GR3: Select the active node-type filter set. Use with useShallow (Set identity). */
export function selectFilterNodeTypes(s: GraphStore): Set<string> {
  return s.filterNodeTypes;
}

/** GR3: Select toggleFilterNodeType action. */
export function selectToggleFilterNodeType(s: GraphStore): GraphActions["toggleFilterNodeType"] {
  return s.toggleFilterNodeType;
}

/** GR4: Select clearFilterNodeTypes action. */
export function selectClearFilterNodeTypes(s: GraphStore): GraphActions["clearFilterNodeTypes"] {
  return s.clearFilterNodeTypes;
}

// ─── Shallow-equality hooks (I3) ─────────────────────────────────────────────

/** Hook: { loading, error } — shallow equality (I3). */
export function useGraphStatus(): { loading: boolean; error: string | null } {
  return useGraphStore(useShallow(selectStatus));
}

/** Hook: { dataVersion, cacheStatus } — shallow equality (I3). */
export function useGraphMeta(): { dataVersion: number | null; cacheStatus: CacheStatus } {
  return useGraphStore(useShallow(selectMeta));
}

// ─── Action selectors ─────────────────────────────────────────────────────────

export function selectSetGraph(s: GraphStore): GraphActions["setGraph"] {
  return s.setGraph;
}

export function selectSetLoading(s: GraphStore): GraphActions["setLoading"] {
  return s.setLoading;
}

export function selectSetError(s: GraphStore): GraphActions["setError"] {
  return s.setError;
}

// ── GI-2 (v1.3.14) visual filter selectors ────────────────────────────────

export function selectHideMetaTypes(s: GraphStore): boolean {
  return s.hideMetaTypes;
}
export function selectHideIsolated(s: GraphStore): boolean {
  return s.hideIsolated;
}
export function selectMinLinks(s: GraphStore): number | null {
  return s.minLinks;
}
export function selectMaxLinks(s: GraphStore): number | null {
  return s.maxLinks;
}
export function selectNodeSizeScale(s: GraphStore): number {
  return s.nodeSizeScale;
}
export function selectSpacingScale(s: GraphStore): number {
  return s.spacingScale;
}

export function selectSetHideMetaTypes(s: GraphStore): GraphActions["setHideMetaTypes"] {
  return s.setHideMetaTypes;
}
export function selectSetHideIsolated(s: GraphStore): GraphActions["setHideIsolated"] {
  return s.setHideIsolated;
}
export function selectSetMinLinks(s: GraphStore): GraphActions["setMinLinks"] {
  return s.setMinLinks;
}
export function selectSetMaxLinks(s: GraphStore): GraphActions["setMaxLinks"] {
  return s.setMaxLinks;
}
export function selectSetNodeSizeScale(s: GraphStore): GraphActions["setNodeSizeScale"] {
  return s.setNodeSizeScale;
}
export function selectSetSpacingScale(s: GraphStore): GraphActions["setSpacingScale"] {
  return s.setSpacingScale;
}
export function selectClearAllGraphFilters(s: GraphStore): GraphActions["clearAllGraphFilters"] {
  return s.clearAllGraphFilters;
}
