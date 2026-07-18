/**
 * homeUtils.ts — shared formatting helpers, color resolvers, and localStorage
 * constants for HomeDashboard sub-components [F18].
 *
 * Keep this file free of React imports — it is imported by multiple sub-components
 * and must remain a plain TypeScript module.
 */

import { pageTypeCssColor } from "../../utils/pageTypeVisuals";

// ─── localStorage keys ─────────────────────────────────────────────────────────

/** localStorage key used to pass a domain filter to the Wiki/NavTree section. */
export const DOMAIN_FILTER_KEY = "synapse:domainFilter";

/**
 * localStorage key used to pass the Louvain community id filter to the Wiki/NavTree.
 * NavTree filters the page list to pages whose community column matches this id.
 */
export const GROUP_FILTER_KEY = "synapse:groupFilter";

/**
 * localStorage key for the human-readable label shown in the NavTree filter banner.
 * Written alongside DOMAIN_FILTER_KEY or GROUP_FILTER_KEY so the banner has a label
 * without a second data fetch.
 */
export const NAV_FILTER_LABEL_KEY = "synapse:navFilterLabel";

/** Custom event dispatched after writing filter keys so a mounted NavTree re-reads them. */
export const NAV_FILTER_EVENT = "synapse:navFilter";

// ─── Formatting helpers ────────────────────────────────────────────────────────

export function formatCost(usd: number): string {
  if (usd === 0) return "$0.00";
  if (usd < 0.01) return "<$0.01";
  return `$${usd.toFixed(2)}`;
}

export function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export function formatUptime(s: number | undefined | null): string {
  if (s == null) return "–";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

// ─── Color helpers ─────────────────────────────────────────────────────────────

/**
 * Single source of truth for page-type colour: the same --syn-type-* tokens the
 * wiki type badges and the graph use.
 */
export function typeColor(type: string): string {
  return pageTypeCssColor(type);
}

/**
 * Color for a REVIEW item type — distinct from page-type colors: review types encode an
 * action/severity, not a content category.
 */
export function reviewTypeColor(itemType: string): string {
  switch (itemType) {
    case "contradiction":
      return "var(--syn-danger)";
    case "duplicate":
      return "var(--syn-warn)";
    case "confirm":
      return "var(--syn-success)";
    case "missing-page":
      return "var(--syn-type-concept)";
    case "purpose-suggestion":
    case "schema-suggestion":
      return "var(--syn-accent2)";
    default:
      return "var(--syn-accent)";
  }
}
