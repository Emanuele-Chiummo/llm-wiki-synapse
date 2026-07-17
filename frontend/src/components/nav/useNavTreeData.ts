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
 *
 * WS-D8 / K1 / I5 — Vault meta section:
 *   A synthetic "vault-meta" group header is appended AFTER all standard wiki sections.
 *   It is shown only when GET /vault/meta returns at least one file.
 *   Each meta file appears as a "meta" row (not a "page" row) so NavTree can open
 *   MetaFileView instead of navigating to a DB-backed NoteView.
 *   If the endpoint 404s or returns an empty list, the section is silently omitted.
 *
 * NavFilter (F18 Home → Wiki drill-down):
 *   Home dashboard section/group cards write a filter to localStorage and dispatch
 *   "synapse:navFilter". This hook reads the filter on mount AND on each event,
 *   filters the fetched page list BEFORE grouping, and exposes filterLabel +
 *   clearFilter so NavTree can render a dismissible banner. I4 is preserved: we
 *   filter the flat array, not the group Map — the virtualizer sees a single flat
 *   result array regardless of filter state.
 *
 *   Filter keys:
 *     "synapse:domainFilter"    — domain name string (from "domain/<name>" tags)
 *     "synapse:groupFilter"     — community id as string (parsed to int)
 *     "synapse:navFilterLabel"  — human-readable label shown in the banner
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchAllPages } from "../../api/pagesClient";
import { fetchVaultMeta } from "../../api/vaultMetaClient";
import type { PageListItem, PageType } from "../../api/types";
import type { VaultMetaFile } from "../../api/vaultMetaClient";

// ─── Tree row model (ADR-0017 §3) ─────────────────────────────────────────────

export type KnownType = PageType | "overview" | "other";

export type TreeRow =
  | { kind: "group"; type: KnownType; count: number; collapsed: boolean }
  | { kind: "page"; id: string; title: string; type: KnownType }
  /** WS-D8: synthetic group header for the vault meta section. */
  | { kind: "vault-meta-group"; count: number }
  /** WS-D8: a single meta file row (schema.md / purpose.md). */
  | { kind: "meta"; file: VaultMetaFile };

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
export function groupPagesByType(items: PageListItem[]): Map<KnownType, PageListItem[]> {
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
 *
 * WS-D8: when metaFiles is non-empty, a "vault-meta-group" header + individual "meta"
 * rows are appended AFTER all standard wiki section rows.
 */
export function flattenTree(
  grouped: Map<KnownType, PageListItem[]>,
  collapsed: Record<string, boolean>,
  metaFiles: VaultMetaFile[] = [],
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

  // Vault meta section — appended after standard wiki sections; omitted when empty.
  if (metaFiles.length > 0) {
    rows.push({ kind: "vault-meta-group", count: metaFiles.length });
    for (const file of metaFiles) {
      rows.push({ kind: "meta", file });
    }
  }

  return rows;
}

// ─── Filter helpers (exported for unit tests) ─────────────────────────────────

/**
 * Reserved vault-level meta pages that must ALWAYS remain visible in the tree,
 * regardless of any active domain/group filter (owner requirement: "Overview deve
 * essere sempre visibile"). These are the vault front-page + auto-generated
 * catalogue/history — they have no domain/community and are relevant in every view.
 */
const ALWAYS_VISIBLE_TYPES = new Set<string>(["overview", "index", "log"]);

function isAlwaysVisible(p: PageListItem): boolean {
  return ALWAYS_VISIBLE_TYPES.has(p.type ?? "");
}

/**
 * Filter a flat page list to those matching a vocabulary domain.
 * A page matches when its `domain` field equals `domainFilter`.
 * Pages with domain == null or undefined are excluded — EXCEPT the reserved meta
 * pages (overview/index/log), which are always kept so the vault front page never
 * disappears behind a filter.
 */
export function filterPagesByDomain(pages: PageListItem[], domainFilter: string): PageListItem[] {
  return pages.filter((p) => isAlwaysVisible(p) || p.domain === domainFilter);
}

/**
 * Filter a flat page list to those belonging to a Louvain community.
 * A page matches when its `community` field equals `communityId`.
 * Pages with community == null or undefined are excluded — EXCEPT the reserved meta
 * pages (overview/index/log), which are always kept.
 */
export function filterPagesByCommunity(pages: PageListItem[], communityId: number): PageListItem[] {
  return pages.filter((p) => isAlwaysVisible(p) || p.community === communityId);
}

// ─── Filter state (localStorage-backed) ───────────────────────────────────────

/** localStorage keys for filter persistence (Home → NavTree drill-down). */
const DOMAIN_FILTER_KEY = "synapse:domainFilter";
const GROUP_FILTER_KEY = "synapse:groupFilter";
const NAV_FILTER_LABEL_KEY = "synapse:navFilterLabel";

/** Custom event name dispatched by Home handlers and clearFilter. */
export const NAV_FILTER_EVENT = "synapse:navFilter";

interface FilterState {
  domainFilter: string | null;
  groupFilter: number | null;
  filterLabel: string | null;
}

/** Read filter state from localStorage (safe — catches SecurityError). */
function readFilters(): FilterState {
  try {
    const domainFilter = localStorage.getItem(DOMAIN_FILTER_KEY) || null;
    const groupStr = localStorage.getItem(GROUP_FILTER_KEY);
    const groupParsed = groupStr !== null ? parseInt(groupStr, 10) : null;
    const groupFilter = groupParsed !== null && !Number.isNaN(groupParsed) ? groupParsed : null;
    const filterLabel = localStorage.getItem(NAV_FILTER_LABEL_KEY) || null;
    return { domainFilter, groupFilter, filterLabel };
  } catch {
    return { domainFilter: null, groupFilter: null, filterLabel: null };
  }
}

// ─── Hook ─────────────────────────────────────────────────────────────────────

export interface NavTreeData {
  rows: TreeRow[];
  loading: boolean;
  error: string | null;
  /** Raw grouped data reflecting the ACTIVE filter (for testing / derived displays) */
  grouped: Map<KnownType, PageListItem[]>;
  /** Vault meta files (schema.md, purpose.md) — empty when endpoint is absent (WS-D8). */
  metaFiles: VaultMetaFile[];
  /** Imperatively re-fetch the page list (used after creating a new page). */
  refresh: () => Promise<void>;
  /**
   * Human-readable label of the active filter (domain name or group label).
   * null when no filter is active — banner should not be rendered.
   */
  filterLabel: string | null;
  /**
   * Clear all active filters: removes localStorage keys, updates state, and
   * dispatches "synapse:navFilter" so any other mounted instance also updates.
   */
  clearFilter: () => void;
}

/**
 * useNavTreeData — fetch pages, group, and flatten into virtualizable TreeRow[].
 *
 * WS-D8: also fetches GET /vault/meta to populate the Vault/Meta section.
 * If that endpoint 404s or returns empty, metaFiles stays [] and the section
 * is silently omitted from the tree (graceful degradation, P0-3 fix).
 *
 * NavFilter: reads domainFilter / groupFilter from localStorage on mount and on
 * "synapse:navFilter" events. Filters the raw page list BEFORE groupPagesByType
 * so each group section reflects only the filtered subset. I4 preserved: the
 * virtual array is always flat — filter reduces its length, never adds nesting.
 *
 * @param vaultId   - Vault to fetch from.
 * @param collapsed - Map of group type → collapsed; drives flattenTree.
 */
export function useNavTreeData(vaultId: string, collapsed: Record<string, boolean>): NavTreeData {
  // Raw flat page list (pre-filter) — updated only on fetch.
  const [allPages, setAllPages] = useState<PageListItem[]>([]);
  const [metaFiles, setMetaFiles] = useState<VaultMetaFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filter state — initialised from localStorage, updated by events.
  const [filters, setFilters] = useState<FilterState>(() => readFilters());

  // Listen for Home-dashboard filter changes (and our own clearFilter dispatch).
  useEffect(() => {
    const handler = () => setFilters(readFilters());
    window.addEventListener(NAV_FILTER_EVENT, handler);
    return () => window.removeEventListener(NAV_FILTER_EVENT, handler);
  }, []);

  const doFetch = useCallback(
    (signal?: AbortSignal) => {
      setLoading(true);
      setError(null);

      // Fetch pages and meta in parallel; meta failure is non-fatal.
      const pagesPromise = fetchAllPages(vaultId, signal);
      // AbortSignal is shared — both abort together on unmount / vaultId change.
      const metaPromise = fetchVaultMeta(vaultId, signal).catch(() => ({
        files: [] as VaultMetaFile[],
      }));

      return Promise.all([pagesPromise, metaPromise])
        .then(([pagesRes, metaRes]) => {
          setAllPages(pagesRes.items);
          setMetaFiles(metaRes.files);
          setLoading(false);
        })
        .catch((err: unknown) => {
          if (err instanceof Error && err.name !== "AbortError") {
            setError(err.message);
            setLoading(false);
          }
        });
    },
    [vaultId],
  );

  useEffect(() => {
    const ctrl = new AbortController();
    void doFetch(ctrl.signal);
    return () => ctrl.abort();
  }, [doFetch]);

  /** Imperatively re-fetch the page list (called after creating/deleting a page). */
  const refresh = useCallback(() => doFetch(), [doFetch]);

  /**
   * Clear all active filters and notify any other mounted instances.
   * I3: removes localStorage keys and calls setFilters — no DOM query, no heavy work.
   */
  const clearFilter = useCallback(() => {
    try {
      localStorage.removeItem(DOMAIN_FILTER_KEY);
      localStorage.removeItem(GROUP_FILTER_KEY);
      localStorage.removeItem(NAV_FILTER_LABEL_KEY);
    } catch {
      // localStorage unavailable — non-fatal
    }
    setFilters({ domainFilter: null, groupFilter: null, filterLabel: null });
    // Notify other mounted instances (same or different component tree).
    window.dispatchEvent(new Event(NAV_FILTER_EVENT));
  }, []);

  // Apply the active filter to the raw page list BEFORE grouping.
  // I4: this is a plain Array.filter — O(n) on the flat list, no DOM mutation.
  const filteredPages = useMemo<PageListItem[]>(() => {
    if (filters.domainFilter !== null) {
      return filterPagesByDomain(allPages, filters.domainFilter);
    }
    if (filters.groupFilter !== null) {
      return filterPagesByCommunity(allPages, filters.groupFilter);
    }
    return allPages;
  }, [allPages, filters]);

  // Group the filtered pages (memoised — rebuilds only when filteredPages changes).
  const grouped = useMemo(() => groupPagesByType(filteredPages), [filteredPages]);

  // Flatten into the virtualizer-ready TreeRow[].
  // Memoize: only rebuild when grouped data, collapse state, or meta files change.
  // Without this, every parent re-render rebuilds the array unnecessarily (I4).
  const rows = useMemo(
    () => flattenTree(grouped, collapsed, metaFiles),
    [grouped, collapsed, metaFiles],
  );

  return {
    rows,
    loading,
    error,
    grouped,
    metaFiles,
    refresh,
    filterLabel: filters.filterLabel,
    clearFilter,
  };
}
