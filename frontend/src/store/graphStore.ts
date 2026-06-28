/**
 * graphStore.ts — Zustand store for Synapse graph state.
 *
 * INVARIANT I3 compliance (pre-compliance for v0.4 chat):
 *   - All components subscribe via SELECTOR FUNCTIONS, never the whole store.
 *   - The useGraphStore hook is exported with a required selector parameter.
 *   - Collections use shallow equality to prevent re-renders on unrelated changes.
 *   - Typed selectors are exported below — components import those, not raw state.
 *
 * See ADR-0015 §3 and docs/sprints/v0.3-architecture.md §7.
 */

import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import type { CacheStatus, GraphEdge, GraphNode } from "../api/types";

// ─── State shape ──────────────────────────────────────────────────────────────

export interface GraphState {
  // Data
  nodes: GraphNode[];
  edges: GraphEdge[];
  dataVersion: number | null;
  cacheStatus: CacheStatus;

  // Loading / error
  loading: boolean;
  error: string | null;

  // Interaction
  selectedNodeId: string | null;

  // Vault
  vaultId: string;
}

// ─── Actions ──────────────────────────────────────────────────────────────────

export interface GraphActions {
  setGraph: (
    nodes: GraphNode[],
    edges: GraphEdge[],
    dataVersion: number,
    cacheStatus: CacheStatus,
  ) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  setSelectedNodeId: (id: string | null) => void;
  setVaultId: (vaultId: string) => void;
  reset: () => void;
}

export type GraphStore = GraphState & GraphActions;

// ─── Initial state ───────────────────────────────────────────────────────────

const INITIAL_STATE: GraphState = {
  nodes: [],
  edges: [],
  dataVersion: null,
  cacheStatus: "unknown",
  loading: false,
  error: null,
  selectedNodeId: null,
  vaultId: (import.meta.env["VITE_DEFAULT_VAULT_ID"] as string | undefined) ?? "default",
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

  setGraph: (nodes, edges, dataVersion, cacheStatus) =>
    set({ nodes, edges, dataVersion, cacheStatus, loading: false, error: null }),

  setLoading: (loading) => set({ loading }),

  setError: (error) => set({ error, loading: false }),

  setSelectedNodeId: (selectedNodeId) => set({ selectedNodeId }),

  setVaultId: (vaultId) => set({ vaultId }),

  reset: () => set(INITIAL_STATE),
}));

// ─── Typed selectors (I3) ─────────────────────────────────────────────────────
//
// Components import these selector functions and pass them to useGraphStore().
// This ensures shallow equality is applied at the right granularity:
//   - scalar selectors use Object.is (Zustand default)
//   - collection selectors use shallow() explicitly
//
// Usage example:
//   const nodes = useGraphStore(selectNodes);           // re-renders only when nodes array ref changes
//   const { loading, error } = useGraphStore(selectStatus); // re-renders only when loading or error change

/** Select the nodes array. Use with useGraphStore — reference is stable across re-renders
 *  unless setGraph is called. */
export function selectNodes(s: GraphStore): GraphNode[] {
  return s.nodes;
}

/** Select the edges array. */
export function selectEdges(s: GraphStore): GraphEdge[] {
  return s.edges;
}

/** Select loading + error status as a shallow-compared object. */
export function selectStatus(s: GraphStore): { loading: boolean; error: string | null } {
  return { loading: s.loading, error: s.error };
}

/** Select cache metadata as a shallow-compared object. */
export function selectMeta(
  s: GraphStore,
): { dataVersion: number | null; cacheStatus: CacheStatus } {
  return { dataVersion: s.dataVersion, cacheStatus: s.cacheStatus };
}

/** Select the currently selected node id (scalar — Object.is equality). */
export function selectSelectedNodeId(s: GraphStore): string | null {
  return s.selectedNodeId;
}

/** Select the vault id (scalar). */
export function selectVaultId(s: GraphStore): string {
  return s.vaultId;
}

// ─── Shallow-equality hooks (I3) ─────────────────────────────────────────────
//
// These hooks wrap useGraphStore with the shallow comparator pre-applied.
// Use them for any selector that returns an object with multiple properties.

/** Hook: { loading, error } — shallow equality (I3: no re-render on unrelated state). */
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

export function selectSetSelectedNodeId(s: GraphStore): GraphActions["setSelectedNodeId"] {
  return s.setSelectedNodeId;
}

export function selectVaultIdAndActions(s: GraphStore): {
  vaultId: string;
  setVaultId: GraphActions["setVaultId"];
} {
  return { vaultId: s.vaultId, setVaultId: s.setVaultId };
}
