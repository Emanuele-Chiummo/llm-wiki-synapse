/**
 * useNavTreeData.test.ts
 *
 * Unit tests for the pure helper functions in useNavTreeData.ts:
 *   - groupPagesByType: buckets and ordering
 *   - flattenTree: group-header rows, page rows, collapsed behavior
 *
 * The hook itself (fetch logic) is integration-tested via Playwright.
 * I4 compliance: flattenTree output size must equal (visible groups + visible pages).
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

  it("omits empty buckets", () => {
    const only = [makePage("x1", "concept")];
    const grouped = groupPagesByType(only);

    expect(grouped.has("concept")).toBe(true);
    expect(grouped.has("entity")).toBe(false);
    expect(grouped.has("source")).toBe(false);
  });

  it("handles empty input", () => {
    const grouped = groupPagesByType([]);
    expect(grouped.size).toBe(0);
  });

  it("preserves canonical TYPE_ORDER for iteration", () => {
    const grouped = groupPagesByType(PAGES);
    const keys = [...grouped.keys()];
    // concept before entity before source before synthesis before comparison before other
    const ci = keys.indexOf("concept");
    const ei = keys.indexOf("entity");
    const si = keys.indexOf("source");
    const syi = keys.indexOf("synthesis");
    const cmpi = keys.indexOf("comparison");
    const oi = keys.indexOf("other");

    expect(ci).toBeLessThan(ei);
    expect(ei).toBeLessThan(si);
    expect(si).toBeLessThan(syi);
    expect(syi).toBeLessThan(cmpi);
    expect(cmpi).toBeLessThan(oi);
  });
});

// ─── flattenTree ──────────────────────────────────────────────────────────────

describe("flattenTree", () => {
  it("emits a group-header row before each group's pages", () => {
    const grouped = groupPagesByType([makePage("c1", "concept")]);
    const rows = flattenTree(grouped, {});

    expect(rows[0]?.kind).toBe("group");
    expect(rows[0]?.kind === "group" && rows[0].type).toBe("concept");
    expect(rows[1]?.kind).toBe("page");
    expect(rows[1]?.kind === "page" && rows[1].id).toBe("c1");
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

    // Only the group header should appear; no page rows
    expect(rows.length).toBe(1);
    expect(rows[0]?.kind).toBe("group");
    expect(rows[0]?.kind === "group" && rows[0].collapsed).toBe(true);
  });

  it("collapsed: false shows pages again", () => {
    const grouped = groupPagesByType([makePage("c1", "concept")]);
    const collapsed = flattenTree(grouped, { concept: true });
    const expanded = flattenTree(grouped, { concept: false });

    expect(collapsed.length).toBe(1);
    expect(expanded.length).toBe(2);
  });

  it("group row carries correct count", () => {
    const grouped = groupPagesByType([
      makePage("c1", "concept"),
      makePage("c2", "concept"),
      makePage("c3", "concept"),
    ]);
    const rows = flattenTree(grouped, {});
    const group = rows[0];

    expect(group?.kind === "group" && group.count).toBe(3);
  });

  it("handles multiple groups with mixed collapse state", () => {
    const grouped = groupPagesByType([
      makePage("c1", "concept"),
      makePage("c2", "concept"),
      makePage("e1", "entity"),
    ]);
    // collapse concept, expand entity
    const rows = flattenTree(grouped, { concept: true, entity: false });

    // concept: 1 header (collapsed) + entity: 1 header + 1 page = 3
    expect(rows.length).toBe(3);

    const types = rows.map((r) => (r.kind === "group" ? `group:${r.type}` : `page:${r.id}`));
    expect(types).toEqual(["group:concept", "group:entity", "page:e1"]);
  });

  it("produces no rows for empty grouped map (I4 boundary)", () => {
    const rows = flattenTree(new Map(), {});
    expect(rows.length).toBe(0);
  });
});
