/**
 * useNavTreeData.ts — fetches GET /pages, groups by type, flattens into TreeRow[].
 *
 * INVARIANT I4: produces a flat array for a single useVirtualizer, so the whole
 * tree (any depth) is one virtualizer pass — no DOM nodes beyond the visible window.
 * INVARIANT I3: does not subscribe to the graph store; pure data hook.
 *
 * Section policy (llm_wiki parity):
 *   - Every STANDARD section (overview, concept, entity, source, synthesis, comparison,
 *     query) is always shown with its count, even when 0.
 *   - The "other" bucket is shown ONLY when non-empty.
 *   - Overview is a singleton page; its section always shows (count 0 until the backend
 *     surfaces overview.md with type "overview" in GET /pages — see flag below).
 */

import { useEffect, useMemo, useState } from "react";
import { fetchAllPages } from "../../api/pagesClient";
import type { PageListItem, PageType } from "../../api/types";

// ─── Tree row model (ADR-0017 §3) ─────────────────────────────────────────────

export type KnownType = PageType | "overview" | "other";

export type TreeRow =
  | { kind: "group"; type: KnownType; count: number; collapsed: boolean }
  | { kind: "page"; id: string; title: string; type: KnownType };

// Canonical ordering — matches llm_wiki section order.
// "overview" first (singleton entry-point), then concepts → entities → sources →
// synthesis → comparisons → queries; "other" is last and only shown when non-empty.
const TYPE_ORDER: KnownType[] = [
  "overview",
  "concept",
  "entity",
  "source",
  "synthesis",
  "comparison",
  "query",
  "other",
];

// Standard sections that must ALWAYS appear (even at count 0).
const ALWAYS_SHOW = new Set<KnownType>([
  "overview",
  "concept",
  "entity",
  "source",
  "synthesis",
  "comparison",
  "query",
]);

function toKnownType(raw: string | null): KnownType {
  if (
    raw === "concept" ||
    raw === "entity" ||
    raw === "source" ||
    raw === "synthesis" ||
    raw === "comparison" ||
    raw === "query" ||
    raw === "overview"
  ) {
    return raw;
  }
  return "other";
}

/**
 * Group a flat page list into a Map<KnownType, PageListItem[]> preserving TYPE_ORDER.
 *
 * Standard sections (ALWAYS_SHOW) are pre-seeded with empty arrays so they always
 * appear in the tree at count 0.  The "other" bucket is retained only when non-empty.
 */
export function groupPagesByType(
  items: PageListItem[],
): Map<KnownType, PageListItem[]> {
  const map = new Map<KnownType, PageListItem[]>();

  // Pre-seed every position in TYPE_ORDER so iteration order is guaranteed.
  for (const t of TYPE_ORDER) {
    map.set(t, []);
  }

  for (const item of items) {
    // Skip raw-source tracking rows (raw/sources/*): they are internal I1/retrieval
    // rows with no title/type and must not appear as a titleless "Other" tree entry.
    // The wiki tree shows wiki pages only (raw sources live under the Sources view).
    if (item.file_path?.startsWith("raw/")) {
      continue;
    }
    const t = toKnownType(item.type);
    const bucket = map.get(t);
    if (bucket) {
      bucket.push(item);
    }
  }

  // Remove empty buckets ONLY for "other" (non-standard section).
  // Standard sections stay at count 0 so they always render.
  for (const t of TYPE_ORDER) {
    if (!ALWAYS_SHOW.has(t) && map.get(t)?.length === 0) {
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

    // fetchAllPages paginates (GET /pages caps at 500) so EVERY page shows in the tree —
    // past 500 pages the oldest ones (incl. the singleton overview) would otherwise vanish.
    fetchAllPages(vaultId, ctrl.signal)
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
