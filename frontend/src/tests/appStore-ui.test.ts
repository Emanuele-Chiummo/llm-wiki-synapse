/**
 * appStore-ui.test.ts
 *
 * Unit tests for the UI slice added in v0.4 Phase 1 (ADR-0017 §4):
 * selectPage, setActiveTab, toggleGroup, and their selectors.
 *
 * I3 compliance: all subscriptions go via typed selectors.
 */

import { describe, it, expect, beforeEach } from "vitest";
import {
  useAppStore,
  selectActiveTab,
  selectTreeCollapsed,
  selectSelectedNodeId,
  selectSelectedSource,
} from "../store/appStore";

// ─── Helpers ──────────────────────────────────────────────────────────────────

function getStore() {
  return useAppStore.getState();
}

beforeEach(() => {
  getStore().reset();
});

// ─── selectPage ───────────────────────────────────────────────────────────────

describe("UI slice — selectPage", () => {
  it("sets selectedNodeId and selectedSource atomically", () => {
    getStore().selectPage("node-1", "tree");

    const s = useAppStore.getState();
    expect(selectSelectedNodeId(s)).toBe("node-1");
    expect(selectSelectedSource(s)).toBe("tree");
  });

  it("distinguishes graph vs tree source", () => {
    getStore().selectPage("node-2", "graph");

    const s = useAppStore.getState();
    expect(selectSelectedNodeId(s)).toBe("node-2");
    expect(selectSelectedSource(s)).toBe("graph");
  });

  it("sets selectedNodeId to null and clears source when passed null", () => {
    getStore().selectPage("node-1", "tree");
    getStore().selectPage(null, "tree");

    const s = useAppStore.getState();
    expect(selectSelectedNodeId(s)).toBeNull();
  });

  it("overwrites previous selection from a different source", () => {
    getStore().selectPage("node-1", "graph");
    getStore().selectPage("node-2", "tree");

    const s = useAppStore.getState();
    expect(selectSelectedNodeId(s)).toBe("node-2");
    expect(selectSelectedSource(s)).toBe("tree");
  });
});

// ─── setActiveTab ─────────────────────────────────────────────────────────────

describe("UI slice — setActiveTab", () => {
  it("starts with 'graph' tab active", () => {
    const s = useAppStore.getState();
    expect(selectActiveTab(s)).toBe("graph");
  });

  it("switches to chat tab", () => {
    getStore().setActiveTab("chat");
    expect(selectActiveTab(useAppStore.getState())).toBe("chat");
  });

  it("switches back to graph tab", () => {
    getStore().setActiveTab("chat");
    getStore().setActiveTab("graph");
    expect(selectActiveTab(useAppStore.getState())).toBe("graph");
  });

  it("idempotent — setting the same tab twice is safe", () => {
    getStore().setActiveTab("graph");
    getStore().setActiveTab("graph");
    expect(selectActiveTab(useAppStore.getState())).toBe("graph");
  });
});

// ─── toggleGroup ──────────────────────────────────────────────────────────────

describe("UI slice — toggleGroup", () => {
  it("starts with empty treeCollapsed", () => {
    const s = useAppStore.getState();
    expect(selectTreeCollapsed(s)).toEqual({});
  });

  it("collapses a group on first toggle", () => {
    getStore().toggleGroup("concept");
    expect(selectTreeCollapsed(useAppStore.getState())["concept"]).toBe(true);
  });

  it("expands a group on second toggle", () => {
    getStore().toggleGroup("concept");
    getStore().toggleGroup("concept");
    expect(selectTreeCollapsed(useAppStore.getState())["concept"]).toBe(false);
  });

  it("toggles independently across group types", () => {
    getStore().toggleGroup("concept");
    getStore().toggleGroup("entity");

    const collapsed = selectTreeCollapsed(useAppStore.getState());
    expect(collapsed["concept"]).toBe(true);
    expect(collapsed["entity"]).toBe(true);
    // Other types remain unset
    expect(collapsed["source"]).toBeUndefined();
  });

  it("does not mutate other group entries", () => {
    getStore().toggleGroup("concept");
    getStore().toggleGroup("entity");
    getStore().toggleGroup("concept"); // un-collapse concept

    const collapsed = selectTreeCollapsed(useAppStore.getState());
    expect(collapsed["concept"]).toBe(false);
    expect(collapsed["entity"]).toBe(true);
  });

  it("reset clears treeCollapsed", () => {
    getStore().toggleGroup("concept");
    getStore().reset();
    expect(selectTreeCollapsed(useAppStore.getState())).toEqual({});
  });
});

// ─── selector isolation (I3) ──────────────────────────────────────────────────

describe("UI slice — selector isolation (I3)", () => {
  it("selectActiveTab does not reference treeCollapsed", () => {
    const tab1 = selectActiveTab(useAppStore.getState());
    getStore().toggleGroup("concept");
    const tab2 = selectActiveTab(useAppStore.getState());
    // Value should be unchanged; reference equality check via Object.is
    expect(tab1).toBe(tab2);
  });

  it("selectTreeCollapsed does not reference activeTab", () => {
    getStore().toggleGroup("entity");
    const c1 = selectTreeCollapsed(useAppStore.getState());
    getStore().setActiveTab("chat");
    const c2 = selectTreeCollapsed(useAppStore.getState());
    // Same reference — treeCollapsed not affected by tab change
    expect(c1).toBe(c2);
  });
});
