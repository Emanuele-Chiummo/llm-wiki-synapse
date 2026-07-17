/**
 * appStore.ts — Zustand store for app-level navigation/selection state (FE-ARCH-6).
 *
 * Extracted out of graphStore.ts, which had become the de-facto app/navigation
 * store (activeSection, vaultId, selectedNodeId/selectedSource, activeTab,
 * treeCollapsed, showInsightsPanel) in addition to owning graph data/filters.
 * That coupling meant navigation-only changes (e.g. switching sections) could
 * in principle trigger re-renders in graph-data subscribers sharing the same
 * store object. This store now owns ONLY navigation/vault/selection state;
 * graphStore owns ONLY graph data + visual filters.
 *
 * INVARIANT I3 compliance:
 *   - All components subscribe via SELECTOR FUNCTIONS, never the whole store.
 *   - Collections (treeCollapsed) use shallow equality to prevent re-renders
 *     on unrelated changes.
 *
 * See ADR-0015 §3, ADR-0017 §4, ADR-0018 §2.
 */

import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";

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
  | "convert"
  | "projects";

/** UI state added in v0.4 Phase 1 shell (F1). */
export interface AppUiState {
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

// ─── State shape ──────────────────────────────────────────────────────────────

export interface AppState extends AppUiState {
  // Vault
  vaultId: string;

  // Selection (single shared key — I3 §4)
  selectedNodeId: string | null;

  /**
   * Whether the GraphInsightsPanel overlay is currently visible.
   * Toggled by the Insights toolbar button in GraphHeader (F4 chrome parity).
   * Scalar: Object.is comparison — no useShallow needed.
   */
  showInsightsPanel: boolean;
}

// ─── Actions ──────────────────────────────────────────────────────────────────

export interface AppActions {
  /**
   * Set selectedNodeId AND record which panel drove the selection.
   * Both tree rows and graph clicks converge on this action so selection
   * stays in a single key and all panels update atomically.
   */
  selectPage: (id: string | null, source: "graph" | "tree") => void;
  setSelectedNodeId: (id: string | null) => void;
  setActiveTab: (tab: CenterTab) => void;
  toggleGroup: (type: string) => void;
  /** Switch the top-level section (ADR-0018 §2). */
  setActiveSection: (section: Section) => void;
  setVaultId: (vaultId: string) => void;
  /** Show or hide the GraphInsightsPanel overlay (F4 chrome parity). */
  setShowInsightsPanel: (show: boolean) => void;
  reset: () => void;
  /**
   * FE-UIUX-3: switch the active vault WITHOUT a full page reload.
   * Adopts the new vaultId and clears any selection/overlay state that
   * referenced the previous vault's data (a node id, graph-vs-tree source,
   * insights overlay). Navigation (activeSection/activeTab) and generic UI
   * prefs (treeCollapsed group keys) are NOT vault-specific and are kept.
   */
  resetForVault: (vaultId: string) => void;
}

export type AppStore = AppState & AppActions;

// ─── Initial state ───────────────────────────────────────────────────────────

const INITIAL_STATE: AppState = {
  selectedNodeId: null,
  vaultId: (import.meta.env["VITE_DEFAULT_VAULT_ID"] as string | undefined) ?? "default",
  selectedSource: null,
  activeTab: "graph",
  treeCollapsed: {},
  activeSection: "home",
  showInsightsPanel: false,
};

// ─── Store ────────────────────────────────────────────────────────────────────

/**
 * useAppStore — always call with a selector.
 *
 * CORRECT:   const vaultId = useAppStore(selectVaultId);
 * FORBIDDEN: const store = useAppStore();  // subscribes to everything → I3 violation
 */
export const useAppStore = create<AppStore>((set) => ({
  ...INITIAL_STATE,

  selectPage: (id, source) => set({ selectedNodeId: id, selectedSource: source }),

  setSelectedNodeId: (selectedNodeId) => set({ selectedNodeId }),

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

  setVaultId: (vaultId) => set({ vaultId }),

  // F4 chrome parity: insights panel toggle
  setShowInsightsPanel: (showInsightsPanel) => set({ showInsightsPanel }),

  reset: () => set(INITIAL_STATE),

  // FE-UIUX-3
  resetForVault: (vaultId) =>
    set({
      vaultId,
      selectedNodeId: null,
      selectedSource: null,
      showInsightsPanel: false,
    }),
}));

// ─── Typed selectors (I3) ─────────────────────────────────────────────────────

/** Select the vault id (scalar). */
export function selectVaultId(s: AppStore): string {
  return s.vaultId;
}

export function selectVaultIdAndActions(s: AppStore): {
  vaultId: string;
  setVaultId: AppActions["setVaultId"];
} {
  return { vaultId: s.vaultId, setVaultId: s.setVaultId };
}

/** FE-UIUX-3: select the resetForVault action. */
export function selectResetForVault(s: AppStore): AppActions["resetForVault"] {
  return s.resetForVault;
}

/** Select the currently selected node id (scalar). */
export function selectSelectedNodeId(s: AppStore): string | null {
  return s.selectedNodeId;
}

export function selectSetSelectedNodeId(s: AppStore): AppActions["setSelectedNodeId"] {
  return s.setSelectedNodeId;
}

/** Select the selectPage action (ADR-0017 §4). */
export function selectSelectPage(s: AppStore): AppActions["selectPage"] {
  return s.selectPage;
}

/** Select selectedSource (scalar). */
export function selectSelectedSource(s: AppStore): AppUiState["selectedSource"] {
  return s.selectedSource;
}

/** Select the active center tab (scalar). */
export function selectActiveTab(s: AppStore): CenterTab {
  return s.activeTab;
}

/** Select the setActiveTab action. */
export function selectSetActiveTab(s: AppStore): AppActions["setActiveTab"] {
  return s.setActiveTab;
}

/** Select the tree-group collapse map (use with useShallow). */
export function selectTreeCollapsed(s: AppStore): Record<string, boolean> {
  return s.treeCollapsed;
}

/** Select the toggleGroup action. */
export function selectToggleGroup(s: AppStore): AppActions["toggleGroup"] {
  return s.toggleGroup;
}

/** Select the active section scalar (Object.is comparison — no useShallow needed). */
export function selectActiveSection(s: AppStore): Section {
  return s.activeSection;
}

/** Select the setActiveSection action. */
export function selectSetActiveSection(s: AppStore): AppActions["setActiveSection"] {
  return s.setActiveSection;
}

/** Select the showInsightsPanel flag (scalar — no useShallow needed). */
export function selectShowInsightsPanel(s: AppStore): boolean {
  return s.showInsightsPanel;
}

/** Select the setShowInsightsPanel action. */
export function selectSetShowInsightsPanel(s: AppStore): AppActions["setShowInsightsPanel"] {
  return s.setShowInsightsPanel;
}

// ─── Shallow-equality hooks (I3) ─────────────────────────────────────────────

/** Hook: treeCollapsed map — shallow equality (I3). */
export function useTreeCollapsed(): Record<string, boolean> {
  return useAppStore(useShallow(selectTreeCollapsed));
}
