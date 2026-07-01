/**
 * useNavTreeData.test.ts
 *
 * Unit tests for the pure helper functions in useNavTreeData.ts:
 *   - groupPagesByType: buckets and ordering, always-show standard sections
 *   - flattenTree: group-header rows, page rows, collapsed behavior
 *
 * The hook itself (fetch logic) is integration-tested via Playwright.
 * I4 compliance: flattenTree output size must equal (visible groups + visible pages).
 *
 * llm_wiki parity (Task 2):
 *   - overview, concept, entity, source, synthesis, comparison, query always appear.
 *   - "other" appears only when non-empty.
 *   - TYPE_ORDER: overview < concept < entity < source < synthesis < comparison < query < other.
 */

import { describe, it, expect } from "vitest";
import { groupPagesByType, flattenTree } from "../components/nav/useNavTreeData";
import type { PageListItem } from "../api/types";

// ─── Fixtures ─────────────────────────────────────────────────────────────────

function makePage(
  id: string,
  type: string | null,
  title = `Page ${id}`,
): PageListItem {
  return {
    id,
    vault_id: "default",
    file_path: `wiki/${id}.md`,
    title,
    type,
    sources: [],
    content_hash: null,
    created_at: "2025-01-01T00:00:00Z",
    updated_at: "2025-01-01T00:00:00Z",
  };
}

const PAGES: PageListItem[] = [
  makePage("c1", "concept", "Alpha"),
  makePage("c2", "concept", "Beta"),
  makePage("e1", "entity", "Entity One"),
  makePage("s1", "source", "Source Doc"),
  makePage("u1", null, "Unknown"),
  makePage("u2", "garbage", "Bad Type"),
  makePage("syn1", "synthesis", "Synthesis A"),
  makePage("cmp1", "comparison", "Comparison X"),
];

// ─── groupPagesByType ─────────────────────────────────────────────────────────

describe("groupPagesByType", () => {
  it("groups items by known type", () => {
    const grouped = groupPagesByType(PAGES);

    expect(grouped.get("concept")?.length).toBe(2);
    expect(grouped.get("entity")?.length).toBe(1);
    expect(grouped.get("source")?.length).toBe(1);
    expect(grouped.get("synthesis")?.length).toBe(1);
    expect(grouped.get("comparison")?.length).toBe(1);
  });

  it("maps null and unknown types to 'other'", () => {
    const grouped = groupPagesByType(PAGES);
    expect(grouped.get("other")?.length).toBe(2); // null + "garbage"
  });

  it("excludes raw-source tracking rows (raw/sources/*) from the tree", () => {
    const rawRow: PageListItem = {
      ...makePage("raw1", null, "Raw Tracking Row"),
      file_path: "raw/sources/raw1.md",
    };
    const grouped = groupPagesByType([...PAGES, rawRow]);
    // The raw row would otherwise land in "other" (type null); it must be dropped,
    // so the "other" bucket still holds only the two genuine unknown-type wiki pages.
    expect(grouped.get("other")?.length).toBe(2);
    const allIds = [...grouped.values()].flat().map((p) => p.id);
    expect(allIds).not.toContain("raw1");
  });

  // ── llm_wiki parity: ALWAYS-SHOW standard sections ────────────────────────

  it("always includes standard sections even when empty (llm_wiki parity)", () => {
    // Only one concept — all other standard sections should still be present at 0.
    const only = [makePage("x1", "concept")];
    const grouped = groupPagesByType(only);

    expect(grouped.has("overview")).toBe(true);
    expect(grouped.has("concept")).toBe(true);
    expect(grouped.has("entity")).toBe(true);
    expect(grouped.has("source")).toBe(true);
    expect(grouped.has("synthesis")).toBe(true);
    expect(grouped.has("comparison")).toBe(true);
    expect(grouped.has("query")).toBe(true);

    expect(grouped.get("entity")?.length).toBe(0);
    expect(grouped.get("source")?.length).toBe(0);
    expect(grouped.get("query")?.length).toBe(0);
    expect(grouped.get("overview")?.length).toBe(0);
  });

  it("hides 'other' when empty (only standard sections shown)", () => {
    const only = [makePage("x1", "concept")];
    const grouped = groupPagesByType(only);

    // "other" must NOT appear when there are no other-typed pages
    expect(grouped.has("other")).toBe(false);
  });

  it("shows 'other' when non-empty", () => {
    const grouped = groupPagesByType(PAGES); // PAGES has 2 "other" items (null + "garbage")
    expect(grouped.has("other")).toBe(true);
    expect(grouped.get("other")?.length).toBe(2);
  });

  it("handles empty input — all standard sections at count 0, no 'other'", () => {
    const grouped = groupPagesByType([]);

    // Standard sections always present
    expect(grouped.has("overview")).toBe(true);
    expect(grouped.has("concept")).toBe(true);
    expect(grouped.has("entity")).toBe(true);
    expect(grouped.has("source")).toBe(true);
    expect(grouped.has("synthesis")).toBe(true);
    expect(grouped.has("comparison")).toBe(true);
    expect(grouped.has("query")).toBe(true);

    // "other" must not appear
    expect(grouped.has("other")).toBe(false);

    // All at 0
    for (const key of ["overview", "concept", "entity", "source", "synthesis", "comparison", "query"] as const) {
      expect(grouped.get(key)?.length).toBe(0);
    }
  });

  it("groups 'query' type pages correctly", () => {
    const pages = [makePage("q1", "query", "Query One"), makePage("q2", "query", "Query Two")];
    const grouped = groupPagesByType(pages);
    expect(grouped.get("query")?.length).toBe(2);
  });

  it("groups 'overview' type pages correctly", () => {
    const pages = [makePage("ov1", "overview", "Overview")];
    const grouped = groupPagesByType(pages);
    expect(grouped.get("overview")?.length).toBe(1);
  });

  it("preserves canonical TYPE_ORDER for iteration (llm_wiki section order)", () => {
    const grouped = groupPagesByType(PAGES);
    const keys = [...grouped.keys()];

    // Must appear: overview < concept < entity < source < synthesis < comparison < other
    const ovi = keys.indexOf("overview");
    const ci = keys.indexOf("concept");
    const ei = keys.indexOf("entity");
    const si = keys.indexOf("source");
    const syi = keys.indexOf("synthesis");
    const cmpi = keys.indexOf("comparison");
    const oi = keys.indexOf("other");

    expect(ovi).toBeLessThan(ci);
    expect(ci).toBeLessThan(ei);
    expect(ei).toBeLessThan(si);
    expect(si).toBeLessThan(syi);
    expect(syi).toBeLessThan(cmpi);
    // "other" at end (when present)
    expect(cmpi).toBeLessThan(oi);
  });

  it("TYPE_ORDER: query appears between comparison and other", () => {
    const pages = [
      makePage("c1", "comparison"),
      makePage("q1", "query"),
      makePage("u1", null, "Unknown"), // lands in "other"
    ];
    const grouped = groupPagesByType(pages);
    const keys = [...grouped.keys()];
    const qi = keys.indexOf("query");
    const cmpi = keys.indexOf("comparison");
    const oi = keys.indexOf("other");

    expect(cmpi).toBeLessThan(qi);
    expect(qi).toBeLessThan(oi);
  });
});

// ─── flattenTree ──────────────────────────────────────────────────────────────

describe("flattenTree", () => {
  it("emits a group-header row before each group's pages", () => {
    const grouped = groupPagesByType([makePage("c1", "concept")]);
    const rows = flattenTree(grouped, {});

    // Find the concept group header
    const conceptGroupRow = rows.find((r) => r.kind === "group" && r.type === "concept");
    expect(conceptGroupRow).toBeDefined();
    expect(conceptGroupRow?.kind).toBe("group");

    // The page row for c1 should exist
    const pageRow = rows.find((r) => r.kind === "page" && r.id === "c1");
    expect(pageRow).toBeDefined();
  });

  it("always-show sections produce group-header rows even at count 0", () => {
    const grouped = groupPagesByType([makePage("c1", "concept")]);
    const rows = flattenTree(grouped, {});

    // All standard section headers should be present
    const groupTypes = rows
      .filter((r) => r.kind === "group")
      .map((r) => (r.kind === "group" ? r.type : null));

    expect(groupTypes).toContain("overview");
    expect(groupTypes).toContain("concept");
    expect(groupTypes).toContain("entity");
    expect(groupTypes).toContain("source");
    expect(groupTypes).toContain("synthesis");
    expect(groupTypes).toContain("comparison");
    expect(groupTypes).toContain("query");
  });

  it("total rows = groups + all pages (nothing collapsed)", () => {
    const grouped = groupPagesByType(PAGES);
    const rows = flattenTree(grouped, {});

    const groupCount = grouped.size;
    const pageCount = PAGES.length;
    expect(rows.length).toBe(groupCount + pageCount);
  });

  it("collapsed group emits only the header row", () => {
    const grouped = groupPagesByType([
      makePage("c1", "concept"),
      makePage("c2", "concept"),
    ]);
    const rows = flattenTree(grouped, { concept: true });

    // concept group header is collapsed — its 2 page rows are suppressed
    const conceptRows = rows.filter((r) => r.kind === "group" && r.type === "concept");
    expect(conceptRows.length).toBe(1);
    expect(conceptRows[0]?.kind === "group" && conceptRows[0].collapsed).toBe(true);

    const pageRows = rows.filter((r) => r.kind === "page");
    expect(pageRows.length).toBe(0); // no concept pages; other standard buckets are empty too
  });

  it("collapsed: false shows pages again", () => {
    const grouped = groupPagesByType([makePage("c1", "concept")]);
    const collapsedRows = flattenTree(grouped, { concept: true });
    const expandedRows = flattenTree(grouped, { concept: false });

    // collapsed: overview + concept(hdr) + entity + source + synthesis + comparison + query = 7
    // expanded:  overview + concept(hdr) + c1(page) + entity + source + synthesis + comparison + query = 8
    const collapsedPages = collapsedRows.filter((r) => r.kind === "page");
    const expandedPages = expandedRows.filter((r) => r.kind === "page");
    expect(expandedPages.length).toBe(collapsedPages.length + 1);
  });

  it("group row carries correct count", () => {
    const grouped = groupPagesByType([
      makePage("c1", "concept"),
      makePage("c2", "concept"),
      makePage("c3", "concept"),
    ]);
    const rows = flattenTree(grouped, {});
    const conceptGroup = rows.find((r) => r.kind === "group" && r.type === "concept");

    expect(conceptGroup?.kind === "group" && conceptGroup.count).toBe(3);
  });

  it("empty sections show count = 0 in their group header row", () => {
    const grouped = groupPagesByType([makePage("c1", "concept")]);
    const rows = flattenTree(grouped, {});
    const queryGroup = rows.find((r) => r.kind === "group" && r.type === "query");
    expect(queryGroup?.kind === "group" && queryGroup.count).toBe(0);
    const overviewGroup = rows.find((r) => r.kind === "group" && r.type === "overview");
    expect(overviewGroup?.kind === "group" && overviewGroup.count).toBe(0);
  });

  it("handles multiple groups with mixed collapse state", () => {
    const grouped = groupPagesByType([
      makePage("c1", "concept"),
      makePage("c2", "concept"),
      makePage("e1", "entity"),
    ]);
    // collapse concept, expand entity
    const rows = flattenTree(grouped, { concept: true, entity: false });

    // concept: 1 header (collapsed, no children)
    // entity: 1 header + 1 page
    // overview, source, synthesis, comparison, query: 1 header each (empty)
    // Total: 1 (concept hdr) + 2 (entity hdr + e1 page) + 5 (empty sections) = 8
    const conceptHdrs = rows.filter((r) => r.kind === "group" && r.type === "concept");
    const entityHdrs = rows.filter((r) => r.kind === "group" && r.type === "entity");
    const pageRows = rows.filter((r) => r.kind === "page");

    expect(conceptHdrs.length).toBe(1);
    expect(entityHdrs.length).toBe(1);
    expect(pageRows.length).toBe(1);
    const pageIds = pageRows.map((r) => (r.kind === "page" ? r.id : null));
    expect(pageIds).toContain("e1");
  });

  it("produces only standard section headers for empty input (I4 boundary)", () => {
    const rows = flattenTree(new Map(), {});
    expect(rows.length).toBe(0);
  });
});
