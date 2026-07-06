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

// ─── Hook ─────────────────────────────────────────────────────────────────────

export interface NavTreeData {
  rows: TreeRow[];
  loading: boolean;
  error: string | null;
  /** Raw grouped data (for testing / derived displays) */
  grouped: Map<KnownType, PageListItem[]>;
  /** Vault meta files (schema.md, purpose.md) — empty when endpoint is absent (WS-D8). */
  metaFiles: VaultMetaFile[];
  /** Imperatively re-fetch the page list (used after creating a new page). */
  refresh: () => Promise<void>;
}

/**
 * useNavTreeData — fetch pages, group, and flatten into virtualizable TreeRow[].
 *
 * WS-D8: also fetches GET /vault/meta to populate the Vault/Meta section.
 * If that endpoint 404s or returns empty, metaFiles stays [] and the section
 * is silently omitted from the tree (graceful degradation, P0-3 fix).
 *
 * @param vaultId   - Vault to fetch from.
 * @param collapsed - Map of group type → collapsed; drives flattenTree.
 */
export function useNavTreeData(
  vaultId: string,
  collapsed: Record<string, boolean>,
): NavTreeData {
  const [grouped, setGrouped] = useState<Map<KnownType, PageListItem[]>>(new Map());
  const [metaFiles, setMetaFiles] = useState<VaultMetaFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const doFetch = useCallback(
    (signal?: AbortSignal) => {
      setLoading(true);
      setError(null);

      // Fetch pages and meta in parallel; meta failure is non-fatal.
      const pagesPromise = fetchAllPages(vaultId, signal);
      // AbortSignal is shared — both abort together on unmount / vaultId change.
      const metaPromise = fetchVaultMeta(vaultId, signal).catch(() => ({ files: [] as VaultMetaFile[] }));

      return Promise.all([pagesPromise, metaPromise])
        .then(([pagesRes, metaRes]) => {
          setGrouped(groupPagesByType(pagesRes.items));
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

  /** Imperative refresh — called after creating/deleting a page so the tree updates. */
  const refresh = useCallback(() => doFetch(), [doFetch]);

  // Memoize: only rebuild the flat array when grouped data, collapse state, or
  // meta files change.  Without this, every parent re-render rebuilds the array
  // unnecessarily (I4 — avoids spurious virtualizer re-measures).
  const rows = useMemo(
    () => flattenTree(grouped, collapsed, metaFiles),
    [grouped, collapsed, metaFiles],
  );

  return { rows, loading, error, grouped, metaFiles, refresh };
}
