/**
 * useNavTreeData.ts — fetches GET /pages, groups by type, flattens into TreeRow[].
 *
 * INVARIANT I4: produces a flat array for a single useVirtualizer, so the whole
 * tree (any depth) is one virtualizer pass — no DOM nodes beyond the visible window.
 * INVARIANT I3: does not subscribe to the graph store; pure data hook.
 */

import { useEffect, useMemo, useState } from "react";
import { fetchPages } from "../../api/pagesClient";
import type { PageListItem, PageType } from "../../api/types";

// ─── Tree row model (ADR-0017 §3) ─────────────────────────────────────────────

export type KnownType = PageType | "other";

export type TreeRow =
  | { kind: "group"; type: KnownType; count: number; collapsed: boolean }
  | { kind: "page"; id: string; title: string; type: KnownType };

// Canonical ordering matches legend + graph palette
const TYPE_ORDER: KnownType[] = [
  "concept",
  "entity",
  "source",
  "synthesis",
  "comparison",
  "other",
];

function toKnownType(raw: string | null): KnownType {
  if (
    raw === "concept" ||
    raw === "entity" ||
    raw === "source" ||
    raw === "synthesis" ||
    raw === "comparison"
  ) {
    return raw;
  }
  return "other";
}

/**
 * Group a flat page list into a Map<KnownType, PageListItem[]> preserving TYPE_ORDER.
 */
export function groupPagesByType(
  items: PageListItem[],
): Map<KnownType, PageListItem[]> {
  const map = new Map<KnownType, PageListItem[]>();
  for (const t of TYPE_ORDER) {
    map.set(t, []);
  }
  for (const item of items) {
    const t = toKnownType(item.type);
    const bucket = map.get(t);
    if (bucket) {
      bucket.push(item);
    }
  }
  // Remove empty buckets so the tree is clean
  for (const t of TYPE_ORDER) {
    if (map.get(t)?.length === 0) {
      map.delete(t);
    }
  }
  return map;
}

/**
 * Flatten a grouped map into a linear TreeRow[] suitable for useVirtualizer.
 * Collapsed groups only emit their header row (children are omitted from the array).
 */
export function flattenTree(
  grouped: Map<KnownType, PageListItem[]>,
  collapsed: Record<string, boolean>,
): TreeRow[] {
  const rows: TreeRow[] = [];
  for (const [type, items] of grouped.entries()) {
    const isCollapsed = collapsed[type] ?? false;
    rows.push({ kind: "group", type, count: items.length, collapsed: isCollapsed });
    if (!isCollapsed) {
      for (const item of items) {
        rows.push({ kind: "page", id: item.id, title: item.title, type });
      }
    }
  }
  return rows;
}

// ─── Hook ─────────────────────────────────────────────────────────────────────

export interface NavTreeData {
  rows: TreeRow[];
  loading: boolean;
  error: string | null;
  /** Raw grouped data (for testing / derived displays) */
  grouped: Map<KnownType, PageListItem[]>;
}

/**
 * useNavTreeData — fetch pages, group, and flatten into virtualizable TreeRow[].
 *
 * @param vaultId   - Vault to fetch from.
 * @param collapsed - Map of group type → collapsed; drives flattenTree.
 */
export function useNavTreeData(
  vaultId: string,
  collapsed: Record<string, boolean>,
): NavTreeData {
  const [grouped, setGrouped] = useState<Map<KnownType, PageListItem[]>>(new Map());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);

    fetchPages(vaultId, { limit: 500 }, ctrl.signal)
      .then((res) => {
        setGrouped(groupPagesByType(res.items));
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (err instanceof Error && err.name !== "AbortError") {
          setError(err.message);
          setLoading(false);
        }
      });

    return () => ctrl.abort();
  }, [vaultId]);

  // Memoize: only rebuild the flat array when grouped data or collapse state changes.
  // Without this, every parent re-render (e.g. store update) rebuilds a fresh array
  // and causes the virtualizer to re-measure unnecessarily.
  const rows = useMemo(() => flattenTree(grouped, collapsed), [grouped, collapsed]);

  return { rows, loading, error, grouped };
}
