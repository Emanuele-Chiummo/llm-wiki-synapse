/**
 * GraphLegend.tsx — CVD-safe node type / community legend overlay.
 * Move-only extraction from GraphViewer.tsx (FE-ARCH-1, 1.9.3 W2) — no behavior change.
 */

import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { LOW_COHESION_THRESHOLD, colorForCommunity } from "../graphPalette";
import type { ColorMode, GraphTheme } from "../graphPalette";
import { communityDisplayName } from "../graphCommunityUtils";
import type { GraphCommunity, GraphNode } from "../../api/types";
import { DEFAULT_NODE_COLOR, TYPE_COLORS } from "./graphViewerShared";

// ─── Legend ───────────────────────────────────────────────────────────────────
// CVD-safe: shows color swatch AND type/community label (redundant encoding, WCAG 1.4.1).

interface GraphLegendProps {
  /** Which color-mode is currently active — determines legend content. */
  colorMode: ColorMode;
  /** Community summary list from GET /graph (server-computed, I2). */
  communities: GraphCommunity[];
  /** Called when a community legend entry is clicked (R9-5). */
  onCommunityClick?: (id: number) => void;
  /**
   * All graph nodes — no longer used for legend rendering in "community" mode
   * (legend now shows per-Louvain-community rows from the communities[] array).
   * Kept for potential future use / backward compat.
   */
  nodes?: GraphNode[];
  /**
   * Resolved app theme (W4 audit FE-GRAPH-1) — selects the light or dark
   * community/domain palette so swatches stay legible on the dark canvas.
   * Defaults to "light" for backward compat.
   */
  theme?: GraphTheme;
}

export const GraphLegend: React.FC<GraphLegendProps> = ({
  colorMode,
  communities,
  onCommunityClick,
  nodes = [],
  theme = "light",
}) => {
  const { t } = useTranslation();
  // Collapsible: the community legend can be tall (one row per Louvain cluster) and cover
  // the graph — the header toggles it. Default expanded.
  const [collapsed, setCollapsed] = useState(false);

  // Per-type node counts for the Node Types legend (llm_wiki parity: "Entity 202" etc.).
  const countsByType = React.useMemo<Record<string, number>>(() => {
    const m: Record<string, number> = {};
    for (const n of nodes) {
      const ty = (n.type as string | null | undefined) ?? "other";
      m[ty] = (m[ty] ?? 0) + 1;
    }
    return m;
  }, [nodes]);
  const otherCount = React.useMemo(
    () => nodes.reduce((acc, n) => (n.type && n.type in TYPE_COLORS ? acc : acc + 1), 0),
    [nodes],
  );

  // COMMUNITY mode = per-Louvain-community rows.
  // One row per community entry in the communities[] array (server-provided, sorted by size desc).
  // Each row is labeled with communityDisplayName(c) — unique because top_page differs per cluster.
  // Low-cohesion communities (< LOW_COHESION_THRESHOLD) get a "!" warning marker.
  // I3: computed via useMemo so it only runs when communities/colorMode change, not per render.
  // I2: communities[] always comes from the server (GET /graph); client never computes Louvain.
  const communityRows = React.useMemo<
    Array<{ community: GraphCommunity; displayName: string; color: string; lowCohesion: boolean }>
  >(() => {
    if (colorMode !== "community") return [];
    return [...communities]
      .sort((a, b) => b.size - a.size)
      .map((c) => ({
        community: c,
        displayName: communityDisplayName(c, (id) => t("graph.legendCommunityLabel", { id })),
        color: colorForCommunity(c.id, theme),
        lowCohesion: c.cohesion < LOW_COHESION_THRESHOLD,
      }));
  }, [colorMode, communities, t, theme]);

  return (
    <div
      className="syn-card"
      style={{
        position: "absolute",
        bottom: 16,
        left: 16,
        padding: "8px 12px",
        zIndex: 5,
        pointerEvents: "none",
        userSelect: "none",
        maxHeight: "calc(100% - 96px)",
        overflowY: "auto",
      }}
      aria-label={colorMode === "type" ? "Graph node type legend" : "Graph community legend"}
      data-testid="graph-legend"
    >
      {/* Collapsible header — pointerEvents:auto so it's clickable inside the pointer-events:none card */}
      <button
        type="button"
        onClick={() => setCollapsed((v) => !v)}
        aria-expanded={!collapsed}
        data-testid="graph-legend-toggle"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          width: "100%",
          background: "none",
          border: "none",
          padding: 0,
          cursor: "pointer",
          pointerEvents: "auto",
          marginBottom: collapsed ? 0 : 6,
          fontSize: 10,
          letterSpacing: "0.08em",
          fontWeight: 600,
          color: "var(--syn-text-muted)",
        }}
      >
        <span aria-hidden="true" style={{ fontSize: 9, width: 8 }}>
          {collapsed ? "▸" : "▾"}
        </span>
        <span>
          {colorMode === "type" ? t("graph.legendNodeTypes") : t("graph.legendCommunities")}
        </span>
      </button>
      {!collapsed &&
        (colorMode === "type" ? (
          <>
            {/* TYPE mode legend — CVD-safe: name + swatch (WCAG 1.4.1).
                Zero-count types are hidden so the legend lists only what's actually on the
                canvas (no "Query 0 · Synthesis 0 · Comparison 0" noise). */}
            {Object.entries(TYPE_COLORS)
              .filter(([type]) => (countsByType[type] ?? 0) > 0)
              .map(([type, color]) => (
                <div
                  key={type}
                  style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 3 }}
                >
                  <span
                    style={{
                      width: 9,
                      height: 9,
                      borderRadius: "50%",
                      background: color,
                      flexShrink: 0,
                      boxShadow: `0 0 0 1px rgba(0,0,0,0.12)`,
                    }}
                    aria-hidden="true"
                  />
                  {/* Redundant encoding: type NAME shown alongside color (WCAG 1.4.1) */}
                  <span
                    style={{ fontSize: 11, color: "var(--syn-text)", textTransform: "capitalize" }}
                  >
                    {type}
                  </span>
                  <span
                    style={{
                      marginLeft: "auto",
                      fontSize: 11,
                      color: "var(--syn-text-dim)",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {countsByType[type] ?? 0}
                  </span>
                </div>
              ))}
            {otherCount > 0 && (
              <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 2 }}>
                <span
                  style={{
                    width: 9,
                    height: 9,
                    borderRadius: "50%",
                    background: DEFAULT_NODE_COLOR,
                    flexShrink: 0,
                    boxShadow: `0 0 0 1px rgba(0,0,0,0.12)`,
                  }}
                  aria-hidden="true"
                />
                <span style={{ fontSize: 11, color: "var(--syn-text-dim)" }}>other</span>
                <span
                  style={{
                    marginLeft: "auto",
                    fontSize: 11,
                    color: "var(--syn-text-dim)",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {otherCount}
                </span>
              </div>
            )}
          </>
        ) : (
          <>
            {/* COMMUNITY mode = per-Louvain-community legend.
              ONE row per community from the communities[] server array, sorted by size desc.
              Each row is labeled with communityDisplayName(c) — unique because each community's
              top_page differs (avoids duplicate "SAM" rows from two same-domain clusters).
              Low-cohesion communities get a "!" warning marker.
              I3: communityRows computed via useMemo above; no work per render frame.
              I2: community data is from server — client never runs Louvain or domain detection. */}
            {communityRows.length === 0 ? (
              <span style={{ fontSize: 11, color: "var(--syn-text-dim)" }}>
                {t("common.unknown")}
              </span>
            ) : (
              communityRows.map(({ community: c, displayName, color, lowCohesion }) => (
                /* G3 (v1.3.14 fix): community rows are now clickable buttons when onCommunityClick is wired.
                 pointerEvents: "auto" overrides the parent card's pointer-events:none so clicks
                 reach the handler. Only active in colorMode==="community" (onCommunityClick is only
                 passed by the parent in that mode). */
                <button
                  key={c.id}
                  type="button"
                  data-testid={`community-legend-item-${c.id}`}
                  onClick={onCommunityClick ? () => onCommunityClick(c.id) : undefined}
                  disabled={!onCommunityClick}
                  aria-label={displayName}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    marginBottom: 4,
                    background: "none",
                    border: "none",
                    padding: "2px 4px",
                    borderRadius: 3,
                    cursor: onCommunityClick ? "pointer" : "default",
                    pointerEvents: "auto",
                    textAlign: "left",
                    width: "100%",
                  }}
                  onMouseEnter={(e) => {
                    if (onCommunityClick) {
                      (e.currentTarget as HTMLButtonElement).style.background =
                        "var(--syn-surface-hover, rgba(0,0,0,0.06))";
                    }
                  }}
                  onMouseLeave={(e) => {
                    (e.currentTarget as HTMLButtonElement).style.background = "none";
                  }}
                >
                  <span
                    style={{
                      width: 9,
                      height: 9,
                      borderRadius: "50%",
                      background: color,
                      flexShrink: 0,
                      boxShadow: `0 0 0 1px rgba(0,0,0,0.12)`,
                    }}
                    aria-hidden="true"
                  />
                  <span
                    style={{
                      fontSize: 11,
                      color: "var(--syn-text)",
                      display: "flex",
                      alignItems: "center",
                      gap: 3,
                    }}
                  >
                    <span data-testid={`community-legend-name-${c.id}`} style={{ fontWeight: 500 }}>
                      {displayName}
                    </span>
                    {lowCohesion && (
                      <span
                        data-testid={`community-legend-low-cohesion-${c.id}`}
                        title={t("graph.legendCommunityLowCohesion")}
                        style={{ color: "var(--syn-amber)", fontSize: 10, lineHeight: 1 }}
                        aria-label={t("graph.legendCommunityLowCohesion")}
                      >
                        !
                      </span>
                    )}
                    <span style={{ color: "var(--syn-text-muted)", marginLeft: 2 }}>
                      {t("graph.legendCommunitySize", { size: c.size })}
                    </span>
                  </span>
                </button>
              ))
            )}
          </>
        ))}
    </div>
  );
};
