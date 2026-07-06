/**
 * graphStore.ts — Zustand store for Synapse graph state + UI slice (F1 / ADR-0017).
 *
 * INVARIANT I3 compliance:
 *   - All components subscribe via SELECTOR FUNCTIONS, never the whole store.
 *   - Collections use shallow equality to prevent re-renders on unrelated changes.
 *   - Typed selectors are exported below — components import those, not raw state.
 *
 * v0.4 extension: a UI slice (selectedSource, activeTab, treeCollapsed) is added
 * as clearly delimited fields on the SAME store. Rationale: selectedNodeId already
 * lives here and is the single shared selection key for graph ↔ tree ↔ preview sync.
 * Splitting into a second store would require cross-store sync for that one key.
 *
 * See ADR-0015 §3, ADR-0017 §4.
 */

import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import type { CacheStatus, GraphCommunity, GraphEdge, GraphNode } from "../api/types";

// ─── UI slice types (ADR-0017 §4, ADR-0018 §2) ───────────────────────────────

/** Which tab is active in the center panel. "chat" is a disabled stub in Phase 1. */
export type CenterTab = "graph" | "chat";

/**
 * Top-level navigation section.
 * ADR-0018 §2 / ADR-0019 §3: "chat" enabled in Phase 3.
 * Scalar, Object.is comparison — no shallow needed.
 * v0.6 [F11]: "sources" added for raw-source file browser; "ingest" retained for run-history.
 * v1.2 [F18][R12-1]: "home" added as the new default landing section.
 */
export type Section =
  | "home"
  | "chat"
  | "pages"
  | "sources"
  | "ingest"
  | "search"
  | "graph"
  | "lint"
  | "review"
  | "deep-search"
  | "settings"
  | "convert";

/** UI state added in v0.4 Phase 1 shell (F1). */
export interface UiState {
  /** Who most recently set the selection (graph click vs tree click). */
  selectedSource: "graph" | "tree" | null;
  /** Active center tab (vestigial in Phase 2; retained for Phase 3 chat placement). */
  activeTab: CenterTab;
  /** Per-group-type collapsed state for the NavTree. */
  treeCollapsed: Record<string, boolean>;
  /**
   * Active top-level navigation section (ADR-0018 §2).
   * Scalar: Object.is comparison — no useShallow needed.
   */
  activeSection: Section;
}

export interface UiActions {
  /**
   * Set selectedNodeId AND record which panel drove the selection.
   * Both tree rows and graph clicks converge on this action so selection
   * stays in a single key and all panels update atomically.
   */
  selectPage: (id: string | null, source: "graph" | "tree") => void;
  setActiveTab: (tab: CenterTab) => void;
  toggleGroup: (type: string) => void;
  /** Switch the top-level section (ADR-0018 §2). */
  setActiveSection: (section: Section) => void;
}

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

  // Loading / error
  loading: boolean;
  error: string | null;

  // Selection (single shared key — I3 §4)
  selectedNodeId: string | null;

  // Vault
  vaultId: string;

  // ── UI slice (ADR-0017 + ADR-0018) ──────────────────────────────────────
  selectedSource: UiState["selectedSource"];
  activeTab: UiState["activeTab"];
  treeCollapsed: UiState["treeCollapsed"];
  activeSection: UiState["activeSection"];
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
  setSelectedNodeId: (id: string | null) => void;
  setVaultId: (vaultId: string) => void;
  reset: () => void;
  /**
   * GR3: Toggle a node type in/out of the active filter set.
   * Empty set = all visible. I2-safe: never touches coords.
   */
  toggleFilterNodeType: (nodeType: string) => void;
  /** GR4: Clear all active type filters. */
  clearFilterNodeTypes: () => void;
  // UI slice actions
  selectPage: UiActions["selectPage"];
  setActiveTab: UiActions["setActiveTab"];
  toggleGroup: UiActions["toggleGroup"];
  setActiveSection: UiActions["setActiveSection"];
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
  loading: false,
  error: null,
  selectedNodeId: null,
  vaultId: (import.meta.env["VITE_DEFAULT_VAULT_ID"] as string | undefined) ?? "default",
  // UI slice defaults
  selectedSource: null,
  activeTab: "graph",
  treeCollapsed: {},
  activeSection: "home" as Section,
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

  setGraph: (nodes, edges, dataVersion, cacheStatus, communities = [], totalNodes = null, totalEdges = null) =>
    set({ nodes, edges, communities, dataVersion, cacheStatus, totalNodes, totalEdges, loading: false, error: null }),

  setLoading: (loading) => set({ loading }),

  setError: (error) => set({ error, loading: false }),

  setSelectedNodeId: (selectedNodeId) => set({ selectedNodeId }),

  setVaultId: (vaultId) => set({ vaultId }),

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

  // ── UI slice actions (ADR-0017 §4) ────────────────────────────────────────

  selectPage: (id, source) => set({ selectedNodeId: id, selectedSource: source }),

  setActiveTab: (activeTab) => set({ activeTab }),

  toggleGroup: (type) =>
    set((s) => ({
      treeCollapsed: {
        ...s.treeCollapsed,
        [type]: !(s.treeCollapsed[type] ?? false),
      },
    })),

  // ADR-0018 §2
  setActiveSection: (activeSection) => set({ activeSection }),
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
export function selectMeta(
  s: GraphStore,
): { dataVersion: number | null; cacheStatus: CacheStatus } {
  return { dataVersion: s.dataVersion, cacheStatus: s.cacheStatus };
}

/** Select the currently selected node id (scalar). */
export function selectSelectedNodeId(s: GraphStore): string | null {
  return s.selectedNodeId;
}

/** Select the vault id (scalar). */
export function selectVaultId(s: GraphStore): string {
  return s.vaultId;
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

// ── UI slice selectors (ADR-0017 §4) ─────────────────────────────────────────

/** Select the active center tab (scalar). */
export function selectActiveTab(s: GraphStore): CenterTab {
  return s.activeTab;
}

/** Select the tree-group collapse map (use with useShallow). */
export function selectTreeCollapsed(s: GraphStore): Record<string, boolean> {
  return s.treeCollapsed;
}

/** Select selectedSource (scalar). */
export function selectSelectedSource(s: GraphStore): UiState["selectedSource"] {
  return s.selectedSource;
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

/** Hook: treeCollapsed map — shallow equality (I3). */
export function useTreeCollapsed(): Record<string, boolean> {
  return useGraphStore(useShallow(selectTreeCollapsed));
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

/** Select the selectPage action (ADR-0017 §4). */
export function selectSelectPage(s: GraphStore): GraphActions["selectPage"] {
  return s.selectPage;
}

/** Select the setActiveTab action. */
export function selectSetActiveTab(s: GraphStore): GraphActions["setActiveTab"] {
  return s.setActiveTab;
}

/** Select the toggleGroup action. */
export function selectToggleGroup(s: GraphStore): GraphActions["toggleGroup"] {
  return s.toggleGroup;
}

// ─── ADR-0018 selectors ───────────────────────────────────────────────────────

/** Select the active section scalar (Object.is comparison — no useShallow needed). */
export function selectActiveSection(s: GraphStore): Section {
  return s.activeSection;
}

/** Select the setActiveSection action. */
export function selectSetActiveSection(s: GraphStore): GraphActions["setActiveSection"] {
  return s.setActiveSection;
}
