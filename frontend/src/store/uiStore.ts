/**
 * uiStore.ts — UI panel open/close state (ADR-0057 §3).
 *
 * Absorbs the desktop collapse state formerly held in PanelGroup's local
 * useState (leftCollapsed / rightCollapsed) and adds mobile/tablet drawer
 * open/close state.
 *
 * INVARIANT I3: all state fields are scalars; Object.is comparison applies,
 * no useShallow needed for individual selectors.
 *
 * Desktop collapse behavior is IDENTICAL to before — the same
 * react-resizable-panels collapse/expand imperative calls stay in PanelGroup;
 * only the state source changes (useState → this store).
 */

import { create } from "zustand";

// ─── State ─────────────────────────────────────────────────────────────────────

export interface UiState {
  /**
   * Desktop: whether the left (tree) panel is expanded.
   * true  = panel visible (not collapsed).
   * false = panel collapsed (hidden via react-resizable-panels).
   */
  leftPanelOpen: boolean;
  /**
   * Desktop: whether the right (preview) panel is expanded.
   */
  rightPanelOpen: boolean;
  /**
   * Mobile: whether the tree drawer is open (slides in from left).
   */
  treeDrawerOpen: boolean;
  /**
   * Mobile/tablet: whether the preview drawer is open (slides in from right).
   */
  previewDrawerOpen: boolean;
}

// ─── Actions ───────────────────────────────────────────────────────────────────

export interface UiActions {
  setLeftPanelOpen: (open: boolean) => void;
  setRightPanelOpen: (open: boolean) => void;
  toggleLeftPanel: () => void;
  toggleRightPanel: () => void;
  openTreeDrawer: () => void;
  closeTreeDrawer: () => void;
  openPreviewDrawer: () => void;
  closePreviewDrawer: () => void;
}

export type UiStore = UiState & UiActions;

// ─── Initial state ─────────────────────────────────────────────────────────────

const INITIAL: UiState = {
  leftPanelOpen: true,
  rightPanelOpen: true,
  treeDrawerOpen: false,
  previewDrawerOpen: false,
};

// ─── Store ─────────────────────────────────────────────────────────────────────

export const useUiStore = create<UiStore>((set) => ({
  ...INITIAL,

  setLeftPanelOpen: (open) => set({ leftPanelOpen: open }),
  setRightPanelOpen: (open) => set({ rightPanelOpen: open }),
  toggleLeftPanel: () => set((s) => ({ leftPanelOpen: !s.leftPanelOpen })),
  toggleRightPanel: () => set((s) => ({ rightPanelOpen: !s.rightPanelOpen })),
  openTreeDrawer: () => set({ treeDrawerOpen: true }),
  closeTreeDrawer: () => set({ treeDrawerOpen: false }),
  openPreviewDrawer: () => set({ previewDrawerOpen: true }),
  closePreviewDrawer: () => set({ previewDrawerOpen: false }),
}));

// ─── Typed selectors ───────────────────────────────────────────────────────────
// These return scalar values — no useShallow needed at the call site.

export const selectLeftPanelOpen = (s: UiStore): boolean => s.leftPanelOpen;
export const selectRightPanelOpen = (s: UiStore): boolean => s.rightPanelOpen;
export const selectTreeDrawerOpen = (s: UiStore): boolean => s.treeDrawerOpen;
export const selectPreviewDrawerOpen = (s: UiStore): boolean => s.previewDrawerOpen;
export const selectSetLeftPanelOpen = (s: UiStore) => s.setLeftPanelOpen;
export const selectSetRightPanelOpen = (s: UiStore) => s.setRightPanelOpen;
export const selectToggleLeftPanel = (s: UiStore) => s.toggleLeftPanel;
export const selectToggleRightPanel = (s: UiStore) => s.toggleRightPanel;
export const selectOpenTreeDrawer = (s: UiStore) => s.openTreeDrawer;
export const selectCloseTreeDrawer = (s: UiStore) => s.closeTreeDrawer;
export const selectOpenPreviewDrawer = (s: UiStore) => s.openPreviewDrawer;
export const selectClosePreviewDrawer = (s: UiStore) => s.closePreviewDrawer;
