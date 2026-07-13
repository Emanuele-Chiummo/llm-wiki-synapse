/**
 * Shared visual contract for wiki page types.
 *
 * DOM surfaces consume the CSS token; Sigma consumes the concrete graph colour
 * because canvas renderers cannot resolve CSS custom properties. Keeping both
 * representations together prevents Home, Review, Preview and Graph from
 * silently assigning different identities to the same generated page type.
 */
export const PAGE_TYPE_VISUALS = {
  concept: { graphColor: "#c084fc" },
  entity: { graphColor: "#60a5fa" },
  source: { graphColor: "#fb923c" },
  synthesis: { graphColor: "#f87171" },
  comparison: { graphColor: "#2dd4bf" },
  query: { graphColor: "#4ade80" },
  overview: { graphColor: "#facc15" },
  index: { graphColor: "#fbbf24" },
  log: { graphColor: "#a78bfa" },
  other: { graphColor: "#94a3b8" },
} as const;

export type VisualPageType = keyof typeof PAGE_TYPE_VISUALS;

export const GRAPH_PAGE_TYPE_ORDER: readonly Exclude<VisualPageType, "other">[] = [
  "concept",
  "entity",
  "source",
  "synthesis",
  "comparison",
  "query",
  "overview",
  "index",
  "log",
] as const;

function normalisePageType(type: string | null | undefined): VisualPageType {
  if (type && Object.prototype.hasOwnProperty.call(PAGE_TYPE_VISUALS, type)) {
    return type as VisualPageType;
  }
  return "other";
}

export function pageTypeCssColor(type: string | null | undefined): string {
  return `var(--syn-type-${normalisePageType(type)})`;
}

export function pageTypeGraphColor(type: string | null | undefined): string {
  return PAGE_TYPE_VISUALS[normalisePageType(type)].graphColor;
}
