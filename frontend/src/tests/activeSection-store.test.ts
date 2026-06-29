/**
 * activeSection-store.test.ts — vitest tests for ADR-0018 §2 extension of graphStore.
 *
 * Tests:
 *   - default activeSection is "chat" (ADR-0018 §2 Phase 3 + F1-HARD-NAV-ORDER, AC-HARD-ORD-2)
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

  it("defaults to 'chat' (F1-HARD-NAV-ORDER / AC-HARD-ORD-2: default section on first load is Chat)", () => {
    const state = useGraphStore.getState();
    expect(selectActiveSection(state)).toBe("chat");
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

  it("reset() brings activeSection back to 'chat' (F1-HARD-NAV-ORDER / AC-HARD-ORD-2)", () => {
    useGraphStore.getState().setActiveSection("settings");
    useGraphStore.getState().reset();
    expect(selectActiveSection(useGraphStore.getState())).toBe("chat");
  });

  it("all 4 valid section values are accepted without throwing", () => {
    const sections: Section[] = ["pages", "graph", "ingest", "settings"];
    for (const section of sections) {
      expect(() => useGraphStore.getState().setActiveSection(section)).not.toThrow();
    }
  });
});
