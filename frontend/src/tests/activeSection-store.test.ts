/**
 * activeSection-store.test.ts — vitest tests for ADR-0018 §2 extension of graphStore.
 *
 * Tests:
 *   - default activeSection is "home" (R12-1: Home dashboard is the new default landing [F18])
 *     NB: was "chat" before v1.2 (AC-HARD-ORD-2 updated per R12-1 AC-R12-1-1)
 *   - setActiveSection transitions all valid sections
 *   - scalar selector returns new value after set
 *   - graph state keys are NOT touched by setActiveSection
 */

import { describe, it, expect, beforeEach } from "vitest";
import { useGraphStore } from "../store/graphStore";
import { selectActiveSection } from "../store/graphStore";
import type { Section } from "../store/graphStore";

describe("graphStore activeSection slice (ADR-0018 §2)", () => {
  beforeEach(() => {
    // Reset store between tests using the reset() action
    useGraphStore.getState().reset();
  });

  it("defaults to 'home' (R12-1 AC-R12-1-1: landing section is Home dashboard [F18])", () => {
    const state = useGraphStore.getState();
    expect(selectActiveSection(state)).toBe("home");
  });

  it("transitions to 'graph'", () => {
    useGraphStore.getState().setActiveSection("graph");
    expect(selectActiveSection(useGraphStore.getState())).toBe("graph");
  });

  it("transitions to 'ingest'", () => {
    useGraphStore.getState().setActiveSection("ingest");
    expect(selectActiveSection(useGraphStore.getState())).toBe("ingest");
  });

  it("transitions to 'settings'", () => {
    useGraphStore.getState().setActiveSection("settings");
    expect(selectActiveSection(useGraphStore.getState())).toBe("settings");
  });

  it("transitions back to 'pages'", () => {
    useGraphStore.getState().setActiveSection("graph");
    useGraphStore.getState().setActiveSection("pages");
    expect(selectActiveSection(useGraphStore.getState())).toBe("pages");
  });

  it("does not modify nodes or edges on setActiveSection", () => {
    const before = useGraphStore.getState();
    const nodesBefore = before.nodes;
    const edgesBefore = before.edges;

    useGraphStore.getState().setActiveSection("graph");

    const after = useGraphStore.getState();
    // Same reference (no mutation) — nodes/edges unchanged
    expect(after.nodes).toBe(nodesBefore);
    expect(after.edges).toBe(edgesBefore);
  });

  it("does not reset activeSection when setGraph is called", () => {
    useGraphStore.getState().setActiveSection("ingest");
    useGraphStore.getState().setGraph([], [], 42, "hit");
    // Section should still be "ingest" — setGraph only touches data fields
    expect(selectActiveSection(useGraphStore.getState())).toBe("ingest");
  });

  it("reset() brings activeSection back to 'home' (R12-1 AC-R12-1-1: default landing is Home [F18])", () => {
    useGraphStore.getState().setActiveSection("settings");
    useGraphStore.getState().reset();
    expect(selectActiveSection(useGraphStore.getState())).toBe("home");
  });

  it("all 5 valid section values are accepted without throwing", () => {
    const sections: Section[] = ["home", "pages", "graph", "ingest", "settings"];
    for (const section of sections) {
      expect(() => useGraphStore.getState().setActiveSection(section)).not.toThrow();
    }
  });
});
