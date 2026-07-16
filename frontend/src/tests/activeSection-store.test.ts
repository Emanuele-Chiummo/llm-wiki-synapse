/**
 * activeSection-store.test.ts — vitest tests for ADR-0018 §2 activeSection slice.
 *
 * FE-ARCH-6: activeSection moved out of graphStore into appStore (navigation/vault/
 * selection state, separate from graph data/filters).
 *
 * Tests:
 *   - default activeSection is "home" (R12-1: Home dashboard is the new default landing [F18])
 *     NB: was "chat" before v1.2 (AC-HARD-ORD-2 updated per R12-1 AC-R12-1-1)
 *   - setActiveSection transitions all valid sections
 *   - scalar selector returns new value after set
 *   - graphStore's data keys are NOT touched by appStore's setActiveSection
 */

import { describe, it, expect, beforeEach } from "vitest";
import { useAppStore, selectActiveSection, type Section } from "../store/appStore";
import { useGraphStore } from "../store/graphStore";

describe("appStore activeSection slice (ADR-0018 §2)", () => {
  beforeEach(() => {
    // Reset both stores between tests using their reset() actions
    useAppStore.getState().reset();
    useGraphStore.getState().reset();
  });

  it("defaults to 'home' (R12-1 AC-R12-1-1: landing section is Home dashboard [F18])", () => {
    const state = useAppStore.getState();
    expect(selectActiveSection(state)).toBe("home");
  });

  it("transitions to 'graph'", () => {
    useAppStore.getState().setActiveSection("graph");
    expect(selectActiveSection(useAppStore.getState())).toBe("graph");
  });

  it("transitions to 'ingest'", () => {
    useAppStore.getState().setActiveSection("ingest");
    expect(selectActiveSection(useAppStore.getState())).toBe("ingest");
  });

  it("transitions to 'settings'", () => {
    useAppStore.getState().setActiveSection("settings");
    expect(selectActiveSection(useAppStore.getState())).toBe("settings");
  });

  it("transitions back to 'pages'", () => {
    useAppStore.getState().setActiveSection("graph");
    useAppStore.getState().setActiveSection("pages");
    expect(selectActiveSection(useAppStore.getState())).toBe("pages");
  });

  it("does not modify graphStore's nodes or edges on setActiveSection (FE-ARCH-6 separation)", () => {
    const before = useGraphStore.getState();
    const nodesBefore = before.nodes;
    const edgesBefore = before.edges;

    useAppStore.getState().setActiveSection("graph");

    const after = useGraphStore.getState();
    // Same reference (no mutation) — nodes/edges unchanged, and live in a separate store
    expect(after.nodes).toBe(nodesBefore);
    expect(after.edges).toBe(edgesBefore);
  });

  it("does not reset activeSection when graphStore.setGraph is called", () => {
    useAppStore.getState().setActiveSection("ingest");
    useGraphStore.getState().setGraph([], [], 42, "hit");
    // Section should still be "ingest" — setGraph only touches graphStore's data fields
    expect(selectActiveSection(useAppStore.getState())).toBe("ingest");
  });

  it("reset() brings activeSection back to 'home' (R12-1 AC-R12-1-1: default landing is Home [F18])", () => {
    useAppStore.getState().setActiveSection("settings");
    useAppStore.getState().reset();
    expect(selectActiveSection(useAppStore.getState())).toBe("home");
  });

  it("all 5 valid section values are accepted without throwing", () => {
    const sections: Section[] = ["home", "pages", "graph", "ingest", "settings"];
    for (const section of sections) {
      expect(() => useAppStore.getState().setActiveSection(section)).not.toThrow();
    }
  });
});
