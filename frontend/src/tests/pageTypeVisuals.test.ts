import { describe, expect, it } from "vitest";

import {
  GRAPH_PAGE_TYPE_ORDER,
  PAGE_TYPE_VISUALS,
  pageTypeCssColor,
  pageTypeGraphColor,
} from "../utils/pageTypeVisuals";

describe("page type visual registry", () => {
  it("covers every generated knowledge-page type", () => {
    for (const type of ["concept", "entity", "source", "synthesis", "comparison", "query"]) {
      expect(PAGE_TYPE_VISUALS).toHaveProperty(type);
      expect(pageTypeCssColor(type)).toBe(`var(--syn-type-${type})`);
      expect(pageTypeGraphColor(type)).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });

  it("keeps graph legend order and unknown fallbacks deterministic", () => {
    expect(GRAPH_PAGE_TYPE_ORDER).toEqual([
      "concept",
      "entity",
      "source",
      "synthesis",
      "comparison",
      "query",
      "overview",
      "index",
      "log",
    ]);
    expect(pageTypeCssColor("unexpected")).toBe("var(--syn-type-other)");
    expect(pageTypeGraphColor(null)).toBe(PAGE_TYPE_VISUALS.other.graphColor);
  });
});
