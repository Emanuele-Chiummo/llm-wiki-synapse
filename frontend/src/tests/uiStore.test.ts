/**
 * uiStore.test.ts — ADR-0057 §3: uiStore actions and selectors.
 *
 * Tests initial state, all actions, and typed selectors.
 * Uses the real Zustand store (no mock) — store is reset between tests.
 */

import { describe, it, expect, beforeEach } from "vitest";
import {
  useUiStore,
  selectLeftPanelOpen,
  selectRightPanelOpen,
  selectTreeDrawerOpen,
  selectPreviewDrawerOpen,
  selectSetLeftPanelOpen,
  selectSetRightPanelOpen,
  selectToggleLeftPanel,
  selectToggleRightPanel,
  selectOpenTreeDrawer,
  selectCloseTreeDrawer,
  selectOpenPreviewDrawer,
  selectClosePreviewDrawer,
} from "../store/uiStore";

// Reset store state before each test by calling the actions to restore defaults.
beforeEach(() => {
  const s = useUiStore.getState();
  s.setLeftPanelOpen(true);
  s.setRightPanelOpen(true);
  s.closeTreeDrawer();
  s.closePreviewDrawer();
});

describe("uiStore — initial state (ADR-0057 §3)", () => {
  it("leftPanelOpen starts as true", () => {
    expect(useUiStore.getState().leftPanelOpen).toBe(true);
  });

  it("rightPanelOpen starts as true", () => {
    expect(useUiStore.getState().rightPanelOpen).toBe(true);
  });

  it("treeDrawerOpen starts as false", () => {
    expect(useUiStore.getState().treeDrawerOpen).toBe(false);
  });

  it("previewDrawerOpen starts as false", () => {
    expect(useUiStore.getState().previewDrawerOpen).toBe(false);
  });
});

describe("uiStore — desktop panel actions", () => {
  it("setLeftPanelOpen(false) collapses left panel", () => {
    const { setLeftPanelOpen } = useUiStore.getState();
    setLeftPanelOpen(false);
    expect(useUiStore.getState().leftPanelOpen).toBe(false);
  });

  it("setLeftPanelOpen(true) expands left panel", () => {
    useUiStore.getState().setLeftPanelOpen(false);
    useUiStore.getState().setLeftPanelOpen(true);
    expect(useUiStore.getState().leftPanelOpen).toBe(true);
  });

  it("setRightPanelOpen(false) collapses right panel", () => {
    const { setRightPanelOpen } = useUiStore.getState();
    setRightPanelOpen(false);
    expect(useUiStore.getState().rightPanelOpen).toBe(false);
  });

  it("toggleLeftPanel flips leftPanelOpen true→false", () => {
    const { toggleLeftPanel } = useUiStore.getState();
    toggleLeftPanel();
    expect(useUiStore.getState().leftPanelOpen).toBe(false);
  });

  it("toggleLeftPanel flips leftPanelOpen false→true", () => {
    useUiStore.getState().setLeftPanelOpen(false);
    useUiStore.getState().toggleLeftPanel();
    expect(useUiStore.getState().leftPanelOpen).toBe(true);
  });

  it("toggleRightPanel flips rightPanelOpen true→false", () => {
    const { toggleRightPanel } = useUiStore.getState();
    toggleRightPanel();
    expect(useUiStore.getState().rightPanelOpen).toBe(false);
  });
});

describe("uiStore — drawer actions", () => {
  it("openTreeDrawer sets treeDrawerOpen=true", () => {
    const { openTreeDrawer } = useUiStore.getState();
    openTreeDrawer();
    expect(useUiStore.getState().treeDrawerOpen).toBe(true);
  });

  it("closeTreeDrawer sets treeDrawerOpen=false", () => {
    useUiStore.getState().openTreeDrawer();
    useUiStore.getState().closeTreeDrawer();
    expect(useUiStore.getState().treeDrawerOpen).toBe(false);
  });

  it("openPreviewDrawer sets previewDrawerOpen=true", () => {
    const { openPreviewDrawer } = useUiStore.getState();
    openPreviewDrawer();
    expect(useUiStore.getState().previewDrawerOpen).toBe(true);
  });

  it("closePreviewDrawer sets previewDrawerOpen=false", () => {
    useUiStore.getState().openPreviewDrawer();
    useUiStore.getState().closePreviewDrawer();
    expect(useUiStore.getState().previewDrawerOpen).toBe(false);
  });

  it("opening tree drawer does not affect preview drawer", () => {
    useUiStore.getState().openTreeDrawer();
    expect(useUiStore.getState().previewDrawerOpen).toBe(false);
  });

  it("opening preview drawer does not affect tree drawer", () => {
    useUiStore.getState().openPreviewDrawer();
    expect(useUiStore.getState().treeDrawerOpen).toBe(false);
  });
});

describe("uiStore — typed selectors", () => {
  it("selectLeftPanelOpen reads leftPanelOpen", () => {
    expect(selectLeftPanelOpen(useUiStore.getState())).toBe(true);
    useUiStore.getState().setLeftPanelOpen(false);
    expect(selectLeftPanelOpen(useUiStore.getState())).toBe(false);
  });

  it("selectRightPanelOpen reads rightPanelOpen", () => {
    expect(selectRightPanelOpen(useUiStore.getState())).toBe(true);
    useUiStore.getState().setRightPanelOpen(false);
    expect(selectRightPanelOpen(useUiStore.getState())).toBe(false);
  });

  it("selectTreeDrawerOpen reads treeDrawerOpen", () => {
    expect(selectTreeDrawerOpen(useUiStore.getState())).toBe(false);
    useUiStore.getState().openTreeDrawer();
    expect(selectTreeDrawerOpen(useUiStore.getState())).toBe(true);
  });

  it("selectPreviewDrawerOpen reads previewDrawerOpen", () => {
    expect(selectPreviewDrawerOpen(useUiStore.getState())).toBe(false);
    useUiStore.getState().openPreviewDrawer();
    expect(selectPreviewDrawerOpen(useUiStore.getState())).toBe(true);
  });

  it("action selectors return the correct functions", () => {
    const state = useUiStore.getState();
    expect(selectSetLeftPanelOpen(state)).toBe(state.setLeftPanelOpen);
    expect(selectSetRightPanelOpen(state)).toBe(state.setRightPanelOpen);
    expect(selectToggleLeftPanel(state)).toBe(state.toggleLeftPanel);
    expect(selectToggleRightPanel(state)).toBe(state.toggleRightPanel);
    expect(selectOpenTreeDrawer(state)).toBe(state.openTreeDrawer);
    expect(selectCloseTreeDrawer(state)).toBe(state.closeTreeDrawer);
    expect(selectOpenPreviewDrawer(state)).toBe(state.openPreviewDrawer);
    expect(selectClosePreviewDrawer(state)).toBe(state.closePreviewDrawer);
  });
});
