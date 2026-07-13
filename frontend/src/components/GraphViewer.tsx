/**
 * GraphViewer.tsx — Obsidian-style sigma.js WebGL knowledge-graph viewer.
 *
 * INVARIANT I2 (ADR-0015 HARD RULE — P0 block):
 *   This component MUST NOT import or call any client-side layout algorithm.
 *   Forbidden: graphology-layout-forceatlas2, d3-force, @antv/layout,
 *              any rAF loop that mutates node x/y.
 *   Node positions come EXCLUSIVELY from the Zustand store (which reflects
 *   the server's precomputed FA2 coords from GET /graph).
 *
 * INVARIANT I4 (ADR-0015 §5):
 *   sigma draws ALL nodes/edges in a SINGLE WebGL canvas.
 *   DOM node count in the graph container is < 20, regardless of graph size.
 *
 * INVARIANT I3 (ADR-0015 §3):
 *   All Zustand subscriptions use typed selectors with shallow equality only.
 *   No whole-store subscription.
 *
 * G2 by construction: no layout → no main-thread long task > 50ms.
 * G4 by construction: single WebGL canvas → bounded DOM (<20 nodes in container).
 *
 * Accessibility:
 *   - container role="application" aria-label="Knowledge graph"
 *   - aria-live="polite" region announces selected node (title, type, neighbor count)
 *   - Label contrast #1f2328 on #ffffff ≈ 18:1 (AAA) — light theme
 *   - CVD-safe type palette with redundant encoding (name in legend + tooltip text)
 *   - prefers-reduced-motion respected for all camera animations
 *
 * LIGHT THEME NOTE (sigma node colors):
 *   sigma.js requires concrete color strings, not CSS custom properties.
 *   TYPE_COLORS below uses hex values that exactly match the --syn-type-* tokens
 *   defined in theme.css. This is the ONE allowed exception to "tokens only"
 *   (documented in ADR-0015 §CVD-SAFE): sigma cannot resolve CSS vars at draw time.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ZoomIn,
  ZoomOut,
  Maximize2,
  RefreshCw,
  Maximize,
  Network,
  Filter,
  RotateCcw,
  Tag,
  Layers,
  Lightbulb,
  Search,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  COMMUNITY_PALETTE,
  LOW_COHESION_THRESHOLD,
  colorForCommunity,
  colorForDomain,
} from "./graphPalette";
import type { ColorMode } from "./graphPalette";
import {
  computeCommunityCentroids,
  computeDomainCentroids,
  communityDisplayName,
} from "./graphCommunityUtils";
import type { CommunityCentroid } from "./graphCommunityUtils";
import Sigma from "sigma";
import type { Attributes } from "graphology-types";
import type { Settings } from "sigma/settings";
import type { NodeDisplayData, PartialButFor } from "sigma/types";
import { buildGraphologyGraph, edgeVisibilityThreshold } from "../api/graphTransform";
import { computeGraphInsights } from "./graph/graphInsights";
import {
  fetchGraph,
  fetchPageDetail,
  patchNodePosition,
  recomputeGraph,
  fetchCommunityDetail,
  fetchEdgeDetail,
} from "../api/graphClient";
import type { CommunityDetail, EdgeDetail } from "../api/graphClient";
import { ApiError } from "../api/graphClient";
import type { GraphCommunity, GraphEdge, GraphNode, PageDetail } from "../api/types";
import {
  selectCommunities,
  selectEdges,
  selectNodes,
  selectSelectedNodeId,
  selectSetError,
  selectSetGraph,
  selectSetLoading,
  selectSetSelectedNodeId,
  selectVaultId,
  selectFilterNodeTypes,
  selectToggleFilterNodeType,
  selectClearFilterNodeTypes,
  selectTotalNodes,
  selectSelectPage,
  selectSetActiveSection,
  selectShowInsightsPanel,
  selectSetShowInsightsPanel,
  selectHideMetaTypes,
  selectHideIsolated,
  selectMinLinks,
  selectMaxLinks,
  selectNodeSizeScale,
  selectSpacingScale,
  selectSetHideMetaTypes,
  selectSetHideIsolated,
  selectSetMinLinks,
  selectSetMaxLinks,
  selectSetNodeSizeScale,
  selectSetSpacingScale,
  selectClearAllGraphFilters,
  useGraphMeta,
  useGraphStatus,
  useGraphStore,
} from "../store/graphStore";
import { useStatusStore, selectStatusDataVersion } from "../store/statusStore";

// ─── Reduced-motion detection ─────────────────────────────────────────────────

const reducedMotion: boolean =
  typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// ─── Resolved theme helpers (ADR-0048 §T1) ───────────────────────────────────
// Read render-only sigma properties from resolved CSS custom properties.
// sigma.js cannot resolve CSS vars at canvas draw time, so we read them via
// getComputedStyle on the document root and pass concrete values to sigma.
// graphPalette.ts (node type/community palette) is NOT touched — ADR-0015 §CVD-SAFE.

interface SigmaThemeColors {
  /** Stage background (sigma container bg) — --syn-bg resolved value */
  bg: string;
  /** Label text color — --syn-text resolved value */
  labelColor: string;
  /** Halo stroke (contrasting surface behind labels) — #ffffff light, #0d1117 dark */
  haloColor: string;
  /** Hover ring stroke — --syn-text resolved value */
  hoverRingColor: string;
}

function readSigmaThemeColors(): SigmaThemeColors {
  try {
    const style = getComputedStyle(document.documentElement);
    const bg = style.getPropertyValue("--syn-bg").trim() || "#ffffff";
    const labelColor = style.getPropertyValue("--syn-text").trim() || "#1f2328";
    // halo: use bg as the contrasting backing stroke so it's visible on the canvas
    const haloColor = bg;
    return { bg, labelColor, haloColor, hoverRingColor: labelColor };
  } catch {
    return {
      bg: "#ffffff",
      labelColor: "#1f2328",
      haloColor: "#ffffff",
      hoverRingColor: "#1f2328",
    };
  }
}

// ─── CVD-safe type palette (spec §CVD-SAFE) ──────────────────────────────────
// Color alone MUST NOT be the only differentiator (WCAG 1.4.1).
// Redundant encoding: legend shows swatch + type NAME; tooltip also shows type text.
//
// Node type palette — aligned to llm_wiki 0.6.0 (Tailwind -400 shades), used for BOTH
// themes exactly as the reference does. sigma.js cannot resolve CSS custom properties at
// canvas draw time, so concrete hex strings are required here. These intentionally do NOT
// track the --syn-type-* badge tokens (which stay tuned for text contrast in lint/wiki
// badges); the GRAPH mirrors the reference palette. Redundant encoding (legend swatch +
// type name + tooltip text) keeps it CVD-safe (WCAG 1.4.1).
//   entity #60a5fa · concept #c084fc · source #fb923c · synthesis #f87171
//   comparison #2dd4bf (teal) · query #4ade80 · overview #facc15 · other #94a3b8 (slate-400)

const TYPE_COLORS: Record<string, string> = {
  concept: "#c084fc", // purple-400 (llm_wiki parity)
  entity: "#60a5fa", // blue-400
  source: "#fb923c", // orange-400
  synthesis: "#f87171", // red-400
  comparison: "#2dd4bf", // teal-400
  query: "#4ade80", // green-400
  overview: "#facc15", // yellow-400
  // G4 (v1.3.14 parity): index and log get dedicated entries instead of falling into "other"
  // index ≈ amber-400 (#fbbf24) — distinct from overview-yellow (#facc15) by hue shift toward orange
  // log   ≈ violet-400 (#a78bfa) — distinct from concept-purple (#c084fc) by lighter/bluer hue
  index: "#fbbf24", // amber-400 (llm_wiki: dedicated Index color)
  log: "#a78bfa", // violet-400 (llm_wiki: dedicated Log color)
};

const DEFAULT_NODE_COLOR = "#94a3b8"; // slate-400 (llm_wiki "other")

function colorForType(type: string | null): string {
  if (type === null) return DEFAULT_NODE_COLOR;
  return TYPE_COLORS[type] ?? DEFAULT_NODE_COLOR;
}

// ─── Re-export community/domain palette + centroid utilities for test isolation ─
// These are all imported from pure modules (no sigma dependency) so they can be
// unit-tested in jsdom without WebGL2. See graphPalette.ts, graphCommunityUtils.ts.
export { COMMUNITY_PALETTE, LOW_COHESION_THRESHOLD, colorForCommunity, colorForDomain };
export type { ColorMode };
// computeCommunityCentroids / computeDomainCentroids / communityDisplayName re-exported
// from graphCommunityUtils for tests that import from GraphViewer directly (backward compat).
export { computeCommunityCentroids, computeDomainCentroids, communityDisplayName };

/**
 * Deepen a hex color by mixing it 30% toward black (#000000).
 * Used in nodeReducer to make neighbor nodes pop more visibly on the light background
 * against the washed-out dimmed nodes.
 * Handles both 3-char (#rgb) and 6-char (#rrggbb) hex; falls back to input on parse error.
 */
function deepenColor(hex: string): string {
  const clean = hex.startsWith("#") ? hex.slice(1) : hex;
  const full = clean.length === 3 ? clean.replace(/./g, (c) => c + c) : clean;
  if (full.length !== 6) return hex;

  const r = parseInt(full.slice(0, 2), 16);
  const g = parseInt(full.slice(2, 4), 16);
  const b = parseInt(full.slice(4, 6), 16);

  if (isNaN(r) || isNaN(g) || isNaN(b)) return hex;

  const mix = 0.3; // 30% toward black
  const dr = Math.round(r * (1 - mix));
  const dg = Math.round(g * (1 - mix));
  const db = Math.round(b * (1 - mix));

  return `#${dr.toString(16).padStart(2, "0")}${dg.toString(16).padStart(2, "0")}${db.toString(16).padStart(2, "0")}`;
}

// ─── Halo label drawer (accessible, AAA contrast) ────────────────────────────
// sigma v3 has no built-in halo; we override defaultDrawNodeLabel.
// Halo color = bg (canvas background for readability); fill = --syn-text.
// Colors are read once per sigma instantiation via readSigmaThemeColors().

type LabelDrawData = PartialButFor<NodeDisplayData, "x" | "y" | "size" | "label" | "color">;

/**
 * Build a halo label drawer that uses the provided theme colors.
 * Called once per sigma instantiation, not per frame.
 */
function makeDrawHaloNodeLabel(themeColors: SigmaThemeColors) {
  return function drawHaloNodeLabel(
    context: CanvasRenderingContext2D,
    data: LabelDrawData,
    settings: Settings<Attributes, Attributes, Attributes>,
  ): void {
    if (!data.label) return;

    const size = settings.labelSize;
    const font = `${settings.labelWeight} ${size}px ${settings.labelFont}`;
    const x = data.x;
    const y = data.y - data.size - 3;

    context.font = font;
    context.textAlign = "center";
    context.textBaseline = "bottom";

    // Halo stroke (bg color) — improves readability on the graph canvas background
    context.strokeStyle = themeColors.haloColor;
    context.lineWidth = 3;
    context.lineJoin = "round";
    context.strokeText(data.label, x, y);

    // Label fill (--syn-text resolved value)
    context.fillStyle = themeColors.labelColor;
    context.fillText(data.label, x, y);
  };
}

/**
 * Build a pill-style hover drawer that uses the provided theme colors.
 * Draws a highlight ring around the hovered node, then a dark rounded-rect
 * "pill" label (reference: nashsu/llm_wiki 0.6.0 pill style).
 *
 * Pill uses rgba(15,20,30,0.88) background with a light border and #f1f5f9 text
 * so it's legible on BOTH light and dark canvas themes without needing CSS vars
 * (sigma draws on an HTMLCanvasRenderingContext2D — no CSS resolution at draw time).
 * The hover ring uses themeColors.hoverRingColor for theme-aware contrast.
 *
 * roundRect: available Chrome 99+, Firefox 112+, Safari 15.4+ (Tauri v2 = WebView2,
 * always Chrome-based). Manual fallback for older Safari.
 */
function makeDrawHaloNodeHover(themeColors: SigmaThemeColors) {
  return function drawHaloNodeHover(
    context: CanvasRenderingContext2D,
    data: LabelDrawData,
    settings: Settings<Attributes, Attributes, Attributes>,
  ): void {
    // Hover ring around the node (render-only; no layout change)
    context.beginPath();
    context.arc(data.x, data.y, data.size + 3, 0, Math.PI * 2);
    context.lineWidth = 2;
    context.strokeStyle = themeColors.hoverRingColor;
    context.stroke();

    if (!data.label) return;

    // ── Pill label ────────────────────────────────────────────────────────────
    const fontSize = settings.labelSize ?? 13;
    const font = `${settings.labelWeight ?? "600"} ${fontSize}px ${settings.labelFont ?? "Inter, system-ui, sans-serif"}`;
    context.font = font;
    const textWidth = context.measureText(data.label).width;

    const padX = 8;
    const padY = 4;
    const pillR = 5; // border-radius
    const boxW = textWidth + padX * 2;
    const boxH = fontSize + padY * 2;
    // Position pill above the node with a gap
    const boxX = data.x - boxW / 2;
    const boxY = data.y - data.size - boxH - 6;

    // Draw pill background (dark in both themes → high contrast with light text)
    context.beginPath();
    if (
      typeof (context as CanvasRenderingContext2D & { roundRect?: (...a: unknown[]) => void })
        .roundRect === "function"
    ) {
      (
        context as CanvasRenderingContext2D & {
          roundRect: (x: number, y: number, w: number, h: number, r: number) => void;
        }
      ).roundRect(boxX, boxY, boxW, boxH, pillR);
    } else {
      // Manual rounded-rect fallback for Safari < 15.4
      const r = Math.min(pillR, boxW / 2, boxH / 2);
      context.moveTo(boxX + r, boxY);
      context.arcTo(boxX + boxW, boxY, boxX + boxW, boxY + boxH, r);
      context.arcTo(boxX + boxW, boxY + boxH, boxX, boxY + boxH, r);
      context.arcTo(boxX, boxY + boxH, boxX, boxY, r);
      context.arcTo(boxX, boxY, boxX + boxW, boxY, r);
      context.closePath();
    }
    context.fillStyle = "rgba(15, 20, 30, 0.88)";
    context.fill();

    // Subtle border (light in both themes — overlaid on dark pill)
    context.strokeStyle = "rgba(255, 255, 255, 0.15)";
    context.lineWidth = 1;
    context.stroke();

    // Label text — always light on the dark pill for maximum readability
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillStyle = "#f1f5f9";
    context.font = font; // reset after stroke (some browsers reset font on stroke)
    context.fillText(data.label, data.x, boxY + boxH / 2);
  };
}

// ─── Node tooltip ─────────────────────────────────────────────────────────────

interface TooltipProps {
  nodeId: string;
  position: { x: number; y: number };
  neighborCount: number;
  onClose: () => void;
}

const NodeTooltip: React.FC<TooltipProps> = ({ nodeId, position, neighborCount, onClose }) => {
  const { t } = useTranslation();
  const [detail, setDetail] = useState<PageDetail | null>(null);
  const [fetching, setFetching] = useState(true);

  useEffect(() => {
    const ctrl = new AbortController();
    setFetching(true);

    fetchPageDetail(nodeId, ctrl.signal)
      .then((d) => {
        setDetail(d);
        setFetching(false);
      })
      .catch((err: unknown) => {
        if (err instanceof Error && err.name !== "AbortError") {
          setFetching(false);
        }
      });

    return () => ctrl.abort();
  }, [nodeId]);

  return (
    <div
      className="syn-card"
      style={{
        position: "absolute",
        left: position.x + 12,
        top: position.y - 8,
        padding: "8px 12px",
        maxWidth: 240,
        zIndex: 10,
        pointerEvents: "none",
      }}
      role="tooltip"
    >
      {fetching ? (
        <span style={{ color: "var(--syn-text-muted)", fontSize: 12 }}>
          {t("graph.tooltip.loading")}
        </span>
      ) : detail !== null ? (
        <>
          <div style={{ fontWeight: 600, fontSize: 13, color: "var(--syn-text)", marginBottom: 4 }}>
            {detail.title}
          </div>
          {detail.type !== null && (
            <div
              style={{
                fontSize: 11,
                color: colorForType(detail.type),
                textTransform: "uppercase",
                letterSpacing: "0.05em",
              }}
            >
              {detail.type}
            </div>
          )}
          <div style={{ fontSize: 11, color: "var(--syn-text-muted)", marginTop: 4 }}>
            {t("graph.tooltip.connections", { count: neighborCount })}
          </div>
        </>
      ) : (
        <span style={{ color: "var(--syn-text-muted)", fontSize: 12 }}>
          {t("graph.tooltip.notFound")}
        </span>
      )}
      <button
        style={{
          position: "absolute",
          top: 4,
          right: 6,
          background: "none",
          border: "none",
          color: "var(--syn-text-dim)",
          cursor: "pointer",
          fontSize: 14,
          lineHeight: 1,
          pointerEvents: "auto",
        }}
        onClick={onClose}
        aria-label={t("graph.tooltip.closeLabel")}
      >
        x
      </button>
    </div>
  );
};

// ─── CommunityPanel (R9-5) ────────────────────────────────────────────────────
// Side panel that opens when a community legend entry is clicked.
// Fetches GET /graph/communities/{id} on demand (I2: read-only, no layout).

interface CommunityPanelProps {
  communityId: number;
  communityColor: string;
  onClose: () => void;
  onNavigate: (pageId: string) => void;
}

const CommunityPanel: React.FC<CommunityPanelProps> = ({
  communityId,
  communityColor,
  onClose,
  onNavigate,
}) => {
  const { t } = useTranslation();
  const [detail, setDetail] = useState<CommunityDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    fetchCommunityDetail(communityId, ctrl.signal)
      .then((d) => {
        setDetail(d);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (err instanceof Error && err.name === "AbortError") return;
        if (err instanceof ApiError && err.status === 409) {
          setError(t("graph.community.coldCache"));
        } else {
          setError(t("graph.community.error"));
        }
        setLoading(false);
      });
    return () => ctrl.abort();
  }, [communityId, t]);

  return (
    <div
      data-testid="community-panel"
      className="syn-card"
      style={{
        position: "absolute",
        top: 12,
        right: 56,
        width: 240,
        maxHeight: "calc(100% - 24px)",
        overflowY: "auto",
        zIndex: 8,
        padding: "12px",
        display: "flex",
        flexDirection: "column",
        gap: 8,
      }}
    >
      {/* Header */}
      <div
        style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6 }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span
            style={{
              width: 10,
              height: 10,
              borderRadius: "50%",
              background: communityColor,
              flexShrink: 0,
              display: "inline-block",
            }}
            aria-hidden="true"
          />
          <span style={{ fontSize: 13, fontWeight: 700, color: "var(--syn-text)" }}>
            {t("graph.community.panelTitle", { id: communityId })}
          </span>
        </div>
        <button
          onClick={onClose}
          aria-label={t("common.close")}
          style={{
            background: "none",
            border: "none",
            color: "var(--syn-text-dim)",
            cursor: "pointer",
            padding: "2px 4px",
            lineHeight: 1,
          }}
        >
          ×
        </button>
      </div>

      {loading && (
        <p style={{ fontSize: 12, color: "var(--syn-text-muted)", margin: 0 }}>
          {t("graph.community.loading")}
        </p>
      )}

      {error !== null && (
        <p style={{ fontSize: 12, color: "var(--syn-red)", margin: 0 }} role="alert">
          {error}
        </p>
      )}

      {detail !== null && (
        <>
          {/* Stats row */}
          <div style={{ fontSize: 12, color: "var(--syn-text-muted)", display: "flex", gap: 12 }}>
            <span>{t("graph.community.memberCount", { count: detail.size })}</span>
            <span data-testid="community-cohesion">
              {t("graph.community.cohesionLabel", { score: detail.cohesion.toFixed(2) })}
            </span>
          </div>

          {/* Low-cohesion warning */}
          {detail.cohesion_warning && (
            <div
              data-testid="community-low-cohesion-warning"
              style={{
                padding: "6px 8px",
                background:
                  "color-mix(in srgb, var(--syn-amber, #d97706) 10%, var(--syn-mix-base) 90%)",
                border:
                  "1px solid color-mix(in srgb, var(--syn-amber, #d97706) 30%, transparent 70%)",
                borderRadius: 4,
                fontSize: 11,
                color: "var(--syn-amber, #d97706)",
                fontWeight: 500,
              }}
            >
              {t("graph.community.lowCohesionWarning")}
            </div>
          )}

          {/* Member list */}
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            {detail.members.length === 0 ? (
              <p style={{ fontSize: 12, color: "var(--syn-text-dim)", margin: 0 }}>
                {t("graph.community.noMembers")}
              </p>
            ) : (
              detail.members.slice(0, 100).map((m) => (
                <button
                  key={m.id}
                  data-testid={`community-member-${m.id}`}
                  onClick={() => onNavigate(m.id)}
                  style={{
                    background: "none",
                    border: "none",
                    padding: "4px 6px",
                    borderRadius: 4,
                    textAlign: "left",
                    cursor: "pointer",
                    color: "var(--syn-text)",
                    fontSize: 12,
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                  }}
                  onMouseEnter={(e) => {
                    (e.currentTarget as HTMLButtonElement).style.background =
                      "var(--syn-surface-hover)";
                  }}
                  onMouseLeave={(e) => {
                    (e.currentTarget as HTMLButtonElement).style.background = "none";
                  }}
                >
                  <span
                    style={{
                      flex: 1,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {m.title}
                  </span>
                  {m.page_type && (
                    <span style={{ fontSize: 10, color: "var(--syn-text-dim)", flexShrink: 0 }}>
                      {m.page_type}
                    </span>
                  )}
                </button>
              ))
            )}
          </div>
        </>
      )}
    </div>
  );
};

// ─── EdgeBreakdownTooltip (R9-5) ─────────────────────────────────────────────
// Popover showing 4-signal edge weight breakdown.
// Fetched on click (edge click in sigma, 150ms debounce on approach not needed for click).
// Cached per (src, tgt) pair in parent component state.

interface EdgeBreakdownTooltipProps {
  srcId: string;
  tgtId: string;
  position: { x: number; y: number };
  cache: Map<string, EdgeDetail>;
  onCached: (key: string, detail: EdgeDetail) => void;
  onClose: () => void;
}

const EdgeBreakdownTooltip: React.FC<EdgeBreakdownTooltipProps> = ({
  srcId,
  tgtId,
  position,
  cache,
  onCached,
  onClose,
}) => {
  const { t } = useTranslation();
  const cacheKey = `${srcId}__${tgtId}`;
  const cached = cache.get(cacheKey) ?? cache.get(`${tgtId}__${srcId}`);
  const [detail, setDetail] = useState<EdgeDetail | null>(cached ?? null);
  const [loading, setLoading] = useState(cached === undefined);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (cached !== undefined) {
      setDetail(cached);
      setLoading(false);
      return;
    }
    const ctrl = new AbortController();
    setLoading(true);
    fetchEdgeDetail(srcId, tgtId, ctrl.signal)
      .then((d) => {
        setDetail(d);
        onCached(cacheKey, d);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (err instanceof Error && err.name === "AbortError") return;
        if (err instanceof ApiError && err.status === 404) {
          setError(t("graph.edge.notFound"));
        } else {
          setError(t("graph.edge.error"));
        }
        setLoading(false);
      });
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [srcId, tgtId]);

  return (
    <div
      data-testid="edge-breakdown-tooltip"
      className="syn-card"
      style={{
        position: "absolute",
        left: position.x + 12,
        top: position.y - 8,
        padding: "10px 14px",
        maxWidth: 280,
        zIndex: 10,
        pointerEvents: "auto",
      }}
      role="tooltip"
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 8,
        }}
      >
        <span style={{ fontSize: 12, fontWeight: 700, color: "var(--syn-text)" }}>
          {t("graph.edge.breakdownTitle")}
        </span>
        <button
          onClick={onClose}
          aria-label={t("common.close")}
          style={{
            background: "none",
            border: "none",
            color: "var(--syn-text-dim)",
            cursor: "pointer",
            padding: "2px 4px",
            lineHeight: 1,
            fontSize: 14,
          }}
        >
          ×
        </button>
      </div>

      {loading && (
        <p style={{ fontSize: 12, color: "var(--syn-text-muted)", margin: 0 }}>
          {t("graph.edge.loading")}
        </p>
      )}
      {error !== null && (
        <p style={{ fontSize: 12, color: "var(--syn-red)", margin: 0 }} role="alert">
          {error}
        </p>
      )}

      {detail !== null && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <EdgeRow label={t("graph.edge.directLinks")} value={detail.breakdown.direct_links} />
          <EdgeRow label={t("graph.edge.sharedSources")} value={detail.breakdown.shared_sources} />
          <EdgeRow label={t("graph.edge.adamicAdar")} value={detail.breakdown.adamic_adar} />
          <EdgeRow label={t("graph.edge.typeAffinity")} value={detail.breakdown.type_affinity} />
          <div style={{ borderTop: "1px solid var(--syn-border)", paddingTop: 4, marginTop: 2 }}>
            <EdgeRow label={t("graph.edge.total")} value={detail.weight} bold />
          </div>
        </div>
      )}
    </div>
  );
};

function EdgeRow({ label, value, bold }: { label: string; value: number; bold?: boolean }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: 11 }}>
      <span
        style={{
          color: bold ? "var(--syn-text)" : "var(--syn-text-muted)",
          fontWeight: bold ? 700 : 400,
        }}
      >
        {label}
      </span>
      <span
        style={{ color: "var(--syn-text)", fontFamily: "monospace", fontWeight: bold ? 700 : 400 }}
      >
        {value.toFixed(3)}
      </span>
    </div>
  );
}

// ─── Edge key helper ──────────────────────────────────────────────────────────
// Canonical order-independent key for an edge pair, used by normalizedWeightMap.
// The server may return edges in either direction; we always normalise to
// sorted order so the map lookup is consistent regardless of (src, tgt) order.
function edgeKey(a: string, b: string): string {
  return a < b ? `${a}__${b}` : `${b}__${a}`;
}

// ─── Meta node types (GI-2 "Hide index / overview / log" filter) ─────────────
// Matches the isMetaNode helper in graphInsights.ts (type-based check, fast in reducers).
const META_NODE_TYPES = new Set(["index", "log", "overview"]);

// ─── Graph node type constants (shared with header filter) ───────────────────
// Must stay in sync with TYPE_COLORS keys above.
const ALL_NODE_TYPES = [
  "concept",
  "entity",
  "source",
  "synthesis",
  "comparison",
  "query",
  "overview",
  "index", // G4 (v1.3.14 parity): dedicated index node type
  "log", // G4 (v1.3.14 parity): dedicated log node type
  "other",
] as const;

// ─── GraphHeader (GR1–GR5, GR7) ─────────────────────────────────────────────
// ONE top toolbar row matching nashsu/llm_wiki 0.6.0 chrome layout.
// LEFT: Network icon + graph.title + stat pills (pages/links/hidden).
// RIGHT: icon-button group — Search (expands), Filter, Reset, Type, Community,
//        Insights (+ count badge), Refresh, Fullscreen.
// Color-mode toggle, regenerate and insights are consolidated here (removed from canvas overlays).
// All operations are client-side (I2 — no layout mutation).

interface GraphHeaderProps {
  /** In-graph nodes from the store payload (GET /graph nodes array) */
  nodes: GraphNode[];
  /** In-graph edges from the store payload (full graph edge set) */
  edges: GraphEdge[];
  /**
   * GR1: All live vault pages (pre-graph-inclusion, from GET /graph total_nodes field).
   * null = old backend that doesn't expose this field yet.
   */
  totalNodes: number | null;
  filterNodeTypes: Set<string>;
  toggleFilterNodeType: (type: string) => void;
  clearFilterNodeTypes: () => void;
  onSearch: (query: string) => void;
  onReset: () => void;
  onFullscreen: () => void;
  graphContainerRef: React.RefObject<HTMLDivElement | null>;
  /** Current color mode — drives active state on Type/Community buttons. */
  colorMode: ColorMode;
  /** Called when the user toggles color mode. */
  onSetColorMode: (mode: ColorMode) => void;
  /** Whether the GraphInsightsPanel overlay is currently visible. */
  showInsights: boolean;
  /** Called when the Insights button is clicked. */
  onToggleInsights: () => void;
  /** Total insight count for the badge (0 = no badge shown). */
  insightCount: number;
  /** Called to regenerate the graph (reconnect + recompute FA2). */
  onRefresh: () => void;
  /** True while regeneration is in flight (spins the RefreshCw icon). */
  regenerating: boolean;
  /** Optional status message to show briefly after regeneration. */
  regenMsg: string | null;
  // ── GI-2 (v1.3.14) visual filter props ─────────────────────────────────
  hideMetaTypes: boolean;
  onSetHideMetaTypes: (v: boolean) => void;
  hideIsolated: boolean;
  onSetHideIsolated: (v: boolean) => void;
  minLinks: number | null;
  onSetMinLinks: (v: number | null) => void;
  maxLinks: number | null;
  onSetMaxLinks: (v: number | null) => void;
  nodeSizeScale: number;
  onSetNodeSizeScale: (v: number) => void;
  spacingScale: number;
  onSetSpacingScale: (v: number) => void;
  /** Clear all active filter state (type + GI-2 fields). Called from the popover "Reset filters" button. */
  onClearAllFilters: () => void;
}

const GraphHeader: React.FC<GraphHeaderProps> = ({
  nodes,
  edges,
  totalNodes,
  filterNodeTypes,
  toggleFilterNodeType,
  clearFilterNodeTypes: _clearFilterNodeTypes,
  onSearch,
  onReset,
  onFullscreen,
  graphContainerRef: _graphContainerRef,
  colorMode,
  onSetColorMode,
  showInsights,
  onToggleInsights,
  insightCount,
  onRefresh,
  regenerating,
  regenMsg,
  // GI-2 (v1.3.14) visual filter props
  hideMetaTypes,
  onSetHideMetaTypes,
  hideIsolated,
  onSetHideIsolated,
  minLinks,
  onSetMinLinks,
  maxLinks,
  onSetMaxLinks,
  nodeSizeScale,
  onSetNodeSizeScale,
  spacingScale,
  onSetSpacingScale,
  onClearAllFilters,
}) => {
  const { t } = useTranslation();
  const [searchQuery, setSearchQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [filterOpen, setFilterOpen] = useState(false);
  const filterRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  // GR1 + GI-2 — PAGES chip: counts nodes passing ALL active filters.
  // visibleNodeSet is computed once and reused for both node count and edge filtering (I3).
  const visibleNodeSet = React.useMemo(() => {
    const s = new Set<string>();
    for (const n of nodes) {
      const typeKey = n.type ?? "other";
      if (filterNodeTypes.size > 0 && !filterNodeTypes.has(typeKey)) continue;
      if (hideMetaTypes && META_NODE_TYPES.has(n.type ?? "")) continue;
      if (hideIsolated && (n.degree ?? 0) === 0) continue;
      if (minLinks !== null && (n.degree ?? 0) < minLinks) continue;
      if (maxLinks !== null && (n.degree ?? 0) > maxLinks) continue;
      s.add(n.id);
    }
    return s;
  }, [nodes, filterNodeTypes, hideMetaTypes, hideIsolated, minLinks, maxLinks]);

  const visibleNodes = visibleNodeSet.size;
  const displayTotalNodes = totalNodes ?? nodes.length;
  const hiddenCount = displayTotalNodes - visibleNodes;

  // GR1 — LINKS chip:
  //   denominator = edges.length (full graph edge set incl. source-overlap, e.g. 4213)
  //   numerator   = edges that are NOT culled by GL1 AND pass the type filter.
  //   GL1-culled edges (normalizedWeight < edgeVisibilityThreshold) count as hidden,
  //   matching nashsu/llm_wiki's "shown/total" display (P3 graph link-chip fix).
  //   GI-2: visibleNodeSet (computed above) is the single source of truth for node
  //   visibility — it accounts for all active filters (type, meta, isolated, min/max links).
  //   Both endpoints of an edge must be in visibleNodeSet for the edge to be counted.

  // GL1 normalised weights for the header chip — same formula as graphTransform.ts.
  // Computed once per edges change; NOT per frame (I3).
  // Key format: "sourceId__targetId" (order-independent via edgeKey helper below).
  const normalizedWeightMap = React.useMemo(() => {
    const m = new Map<string, number>();
    if (edges.length === 0) return m;
    let wMin = Infinity;
    let wMax = -Infinity;
    for (const e of edges) {
      if (e.weight < wMin) wMin = e.weight;
      if (e.weight > wMax) wMax = e.weight;
    }
    const range = wMax - wMin;
    for (const e of edges) {
      const nw = range === 0 ? 0.5 : (e.weight - wMin) / range;
      m.set(edgeKey(e.source, e.target), nw);
    }
    return m;
  }, [edges]);

  const visibleEdges = React.useMemo(() => {
    // GL1: edges below edgeVisibilityThreshold(nodeCount) are culled at rest.
    // The chip numerator must exclude GL1-culled edges AND edges whose endpoints are
    // filtered out by ANY active filter (type, meta, isolated, min/max links).
    // GI-2: use visibleNodeSet (which accounts for all active filters) instead of
    // a type-only check. Both endpoints must be in visibleNodeSet.
    // INVARIANT I2: read-only; no coord mutation.
    const threshold = edgeVisibilityThreshold(nodes.length);
    return edges.filter((e) => {
      const nw = normalizedWeightMap.get(edgeKey(e.source, e.target)) ?? 0.5;
      if (nw < threshold) return false;
      return visibleNodeSet.has(e.source) && visibleNodeSet.has(e.target);
    }).length;
  }, [edges, visibleNodeSet, nodes.length, normalizedWeightMap]);

  const totalEdgesCount = edges.length;

  // GR2: search handler — called on input change; finds the first matching node
  const handleSearchChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const q = e.target.value;
      setSearchQuery(q);
      onSearch(q);
    },
    [onSearch],
  );

  const handleSearchClear = useCallback(() => {
    setSearchQuery("");
    onSearch("");
  }, [onSearch]);

  const handleSearchKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Escape") {
        setSearchQuery("");
        onSearch("");
        setSearchOpen(false);
        (e.currentTarget as HTMLInputElement).blur();
      }
    },
    [onSearch],
  );

  // Focus the search input when the search panel opens
  useEffect(() => {
    if (searchOpen && searchInputRef.current) {
      searchInputRef.current.focus();
    }
  }, [searchOpen]);

  // GR3: close filter popover on outside click
  useEffect(() => {
    if (!filterOpen) return;
    const handler = (e: MouseEvent) => {
      if (filterRef.current && !filterRef.current.contains(e.target as Node)) {
        setFilterOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [filterOpen]);

  // GI-2: Filter button highlights when ANY filter is active (type, meta, isolated, links, size, spacing)
  const hasActiveFilter =
    filterNodeTypes.size > 0 ||
    hideMetaTypes ||
    hideIsolated ||
    minLinks !== null ||
    maxLinks !== null ||
    nodeSizeScale !== 1.0 ||
    spacingScale !== 1.0;
  const searchActive = searchQuery.length > 0;

  // Shared style factory for icon-only toolbar buttons (ghost / active states).
  // Following the reference: ghost = subtle bg + muted text; active = accent tint.
  const iconBtnStyle = (active = false, disabled = false): React.CSSProperties => ({
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    flexShrink: 0,
    padding: 0,
    width: 26,
    height: 26,
    border: `1px solid ${active ? "var(--syn-accent)" : "var(--syn-border)"}`,
    borderRadius: 4,
    background: active
      ? "color-mix(in srgb, var(--syn-accent) 12%, var(--syn-surface))"
      : "var(--syn-surface)",
    color: active ? "var(--syn-accent)" : "var(--syn-text-muted)",
    cursor: disabled ? "default" : "pointer",
    opacity: disabled ? 0.55 : 1,
  });

  return (
    <div
      data-testid="graph-header"
      role="toolbar"
      aria-label={t("graph.title")}
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 8,
        padding: "5px 10px",
        borderBottom: "1px solid var(--syn-border)",
        background: "var(--syn-surface)",
        flexShrink: 0,
        minHeight: 38,
        // Must be above canvas overlays
        position: "relative",
        zIndex: 15,
      }}
    >
      {/* LEFT: Network icon + title + stat pills ───────────────────────────── */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0, minWidth: 0 }}>
        {/* Title */}
        <div style={{ display: "flex", alignItems: "center", gap: 5, flexShrink: 0 }}>
          <Network
            size={14}
            style={{ color: "var(--syn-text-muted)", flexShrink: 0 }}
            aria-hidden="true"
          />
          <span
            style={{
              fontSize: 12,
              fontWeight: 600,
              color: "var(--syn-text)",
              whiteSpace: "nowrap",
            }}
          >
            {t("graph.title")}
          </span>
        </div>

        {/* GR1 stat pills — pages/links/hidden */}
        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <span
            data-testid="graph-header-nodes"
            style={{
              fontSize: 11,
              color: "var(--syn-text-muted)",
              background: "color-mix(in srgb, var(--syn-border) 40%, transparent)",
              borderRadius: 3,
              padding: "1px 5px",
              whiteSpace: "nowrap",
            }}
          >
            {visibleNodes}/{displayTotalNodes} {t("graph.header.pages")}
          </span>
          {totalEdgesCount > 0 && (
            <span
              data-testid="graph-header-edges"
              style={{
                fontSize: 11,
                color: "var(--syn-text-muted)",
                background: "color-mix(in srgb, var(--syn-border) 40%, transparent)",
                borderRadius: 3,
                padding: "1px 5px",
                whiteSpace: "nowrap",
              }}
            >
              {visibleEdges}/{totalEdgesCount} {t("graph.header.links")}
            </span>
          )}
          {hiddenCount > 0 && (
            <span
              data-testid="graph-header-hidden"
              style={{
                fontSize: 11,
                color: "#d97706",
                background: "color-mix(in srgb, #d97706 12%, transparent)",
                border: "1px solid color-mix(in srgb, #d97706 30%, transparent)",
                borderRadius: 3,
                padding: "1px 5px",
                whiteSpace: "nowrap",
              }}
            >
              {hiddenCount} {t("graph.header.hidden")}
            </span>
          )}
        </div>
      </div>

      {/* RIGHT: control icon buttons ────────────────────────────────────────── */}
      <div style={{ display: "flex", alignItems: "center", gap: 3, flexShrink: 0 }}>
        {/* GR2: Search — expanding input; collapses back to icon when closed */}
        {(searchOpen || searchActive) && (
          <div style={{ position: "relative", flexShrink: 0, marginRight: 2 }}>
            <Search
              size={12}
              style={{
                position: "absolute",
                left: 7,
                top: "50%",
                transform: "translateY(-50%)",
                color: "var(--syn-text-muted)",
                pointerEvents: "none",
              }}
              aria-hidden="true"
            />
            <input
              ref={searchInputRef}
              data-testid="graph-search-input"
              type="text"
              value={searchQuery}
              onChange={handleSearchChange}
              onKeyDown={handleSearchKeyDown}
              placeholder={t("graph.header.searchPlaceholder")}
              aria-label={t("graph.header.searchPlaceholder")}
              style={{
                fontSize: 11,
                padding: "3px 22px 3px 22px",
                border: "1px solid var(--syn-border)",
                borderRadius: 4,
                background: "var(--syn-bg)",
                color: "var(--syn-text)",
                width: 160,
                outline: "none",
              }}
            />
            {/* Clear / collapse button */}
            <button
              type="button"
              onClick={() => {
                if (searchActive) {
                  handleSearchClear();
                } else {
                  setSearchOpen(false);
                }
              }}
              aria-label={searchActive ? t("graph.header.clearFilter") : t("common.close")}
              style={{
                position: "absolute",
                right: 4,
                top: "50%",
                transform: "translateY(-50%)",
                background: "none",
                border: "none",
                color: "var(--syn-text-dim)",
                cursor: "pointer",
                padding: 0,
                display: "flex",
                alignItems: "center",
                lineHeight: 1,
              }}
            >
              <X size={11} aria-hidden="true" />
            </button>
          </div>
        )}

        {/* Search toggle icon */}
        <button
          type="button"
          data-testid="graph-search-toggle"
          onClick={() => setSearchOpen((o) => !o)}
          aria-label={t("graph.header.searchPlaceholder")}
          title={t("graph.header.searchPlaceholder")}
          style={iconBtnStyle(searchOpen || searchActive)}
        >
          <Search size={13} strokeWidth={1.8} aria-hidden="true" />
        </button>

        {/* GR3: Filter — icon button with expanding popover */}
        <div ref={filterRef} style={{ position: "relative", flexShrink: 0 }}>
          {/* G1 (v1.3.14 parity): icon + text label, matching llm_wiki toolbar style */}
          <button
            type="button"
            data-testid="graph-filter-button"
            onClick={() => setFilterOpen((o) => !o)}
            aria-label={t("graph.header.filter")}
            title={t("graph.header.filter")}
            aria-expanded={filterOpen}
            style={{
              ...iconBtnStyle(hasActiveFilter || filterOpen),
              width: "auto",
              padding: "0 6px",
              gap: 4,
            }}
          >
            <Filter size={13} strokeWidth={1.8} aria-hidden="true" />
            <span style={{ fontSize: 11, whiteSpace: "nowrap" }}>{t("graph.header.filter")}</span>
          </button>

          {filterOpen && (
            /* GI-2 (v1.3.14): expanded filter popover matching llm_wiki panel structure.
               Sections: (1) Quick filters, (2) Min/Max links, (3) Display tuning,
               (4) Node types (existing), (5) Summary line. */
            <div
              data-testid="graph-filter-popover"
              style={{
                position: "absolute",
                top: "calc(100% + 4px)",
                right: 0,
                background: "var(--syn-surface)",
                border: "1px solid var(--syn-border)",
                borderRadius: 6,
                padding: "8px 10px",
                zIndex: 20,
                minWidth: 200,
                maxWidth: 280,
                boxShadow: "0 4px 16px rgba(0,0,0,0.12)",
              }}
            >
              {/* ── 1. Quick Filters ── */}
              <div
                style={{
                  fontSize: 10,
                  color: "var(--syn-text-muted)",
                  letterSpacing: "0.06em",
                  fontWeight: 600,
                  marginBottom: 5,
                }}
              >
                {t("graph.filter.quickFilters")}
              </div>
              <label
                data-testid="graph-filter-hide-meta"
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "3px 2px",
                  cursor: "pointer",
                  fontSize: 11,
                  color: "var(--syn-text)",
                  marginBottom: 2,
                }}
              >
                <input
                  type="checkbox"
                  checked={hideMetaTypes}
                  onChange={() => onSetHideMetaTypes(!hideMetaTypes)}
                  style={{ width: 12, height: 12, cursor: "pointer", flexShrink: 0 }}
                />
                {t("graph.filter.hideMeta")}
              </label>
              <label
                data-testid="graph-filter-hide-isolated"
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "3px 2px",
                  cursor: "pointer",
                  fontSize: 11,
                  color: "var(--syn-text)",
                }}
              >
                <input
                  type="checkbox"
                  checked={hideIsolated}
                  onChange={() => onSetHideIsolated(!hideIsolated)}
                  style={{ width: 12, height: 12, cursor: "pointer", flexShrink: 0 }}
                />
                {t("graph.filter.hideIsolated")}
              </label>

              {/* ── 2. Min / Max Links ── */}
              <div
                style={{
                  fontSize: 10,
                  color: "var(--syn-text-muted)",
                  letterSpacing: "0.06em",
                  fontWeight: 600,
                  marginTop: 8,
                  marginBottom: 4,
                }}
              >
                {t("graph.filter.minLinks")} / {t("graph.filter.maxLinks")}
              </div>
              <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <input
                  type="number"
                  data-testid="graph-filter-min-links"
                  value={minLinks ?? ""}
                  placeholder={t("graph.filter.any")}
                  min={0}
                  onChange={(e) => {
                    const v = e.target.value.trim();
                    onSetMinLinks(v === "" ? null : Math.max(0, parseInt(v, 10)));
                  }}
                  aria-label={t("graph.filter.minLinks")}
                  style={{
                    width: 72,
                    fontSize: 11,
                    padding: "2px 4px",
                    border: "1px solid var(--syn-border)",
                    borderRadius: 3,
                    background: "var(--syn-bg)",
                    color: "var(--syn-text)",
                    outline: "none",
                  }}
                />
                <span style={{ fontSize: 11, color: "var(--syn-text-dim)" }}>–</span>
                <input
                  type="number"
                  data-testid="graph-filter-max-links"
                  value={maxLinks ?? ""}
                  placeholder={t("graph.filter.any")}
                  min={0}
                  onChange={(e) => {
                    const v = e.target.value.trim();
                    onSetMaxLinks(v === "" ? null : Math.max(0, parseInt(v, 10)));
                  }}
                  aria-label={t("graph.filter.maxLinks")}
                  style={{
                    width: 72,
                    fontSize: 11,
                    padding: "2px 4px",
                    border: "1px solid var(--syn-border)",
                    borderRadius: 3,
                    background: "var(--syn-bg)",
                    color: "var(--syn-text)",
                    outline: "none",
                  }}
                />
              </div>

              {/* ── 3. Display Tuning ── */}
              <div
                style={{
                  fontSize: 10,
                  color: "var(--syn-text-muted)",
                  letterSpacing: "0.06em",
                  fontWeight: 600,
                  marginTop: 8,
                  marginBottom: 4,
                }}
              >
                {t("graph.filter.displayTuning")}
              </div>
              <div style={{ marginBottom: 4 }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 2 }}>
                  <span style={{ fontSize: 11, color: "var(--syn-text)" }}>
                    {t("graph.filter.nodeSize")}
                  </span>
                  <span
                    style={{
                      fontSize: 10,
                      color: "var(--syn-text-muted)",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {Math.round(nodeSizeScale * 100)}%
                  </span>
                </div>
                <input
                  type="range"
                  data-testid="graph-filter-node-size"
                  min={0}
                  max={2}
                  step={0.05}
                  value={nodeSizeScale}
                  onChange={(e) => onSetNodeSizeScale(parseFloat(e.target.value))}
                  aria-label={t("graph.filter.nodeSize")}
                  style={{ width: "100%", cursor: "pointer" }}
                />
              </div>
              <div style={{ marginBottom: 4 }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 2 }}>
                  <span style={{ fontSize: 11, color: "var(--syn-text)" }}>
                    {t("graph.filter.spacing")}
                  </span>
                  <span
                    style={{
                      fontSize: 10,
                      color: "var(--syn-text-muted)",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {Math.round(spacingScale * 100)}%
                  </span>
                </div>
                <input
                  type="range"
                  data-testid="graph-filter-spacing"
                  min={0}
                  max={2}
                  step={0.05}
                  value={spacingScale}
                  onChange={(e) => onSetSpacingScale(parseFloat(e.target.value))}
                  aria-label={t("graph.filter.spacing")}
                  style={{ width: "100%", cursor: "pointer" }}
                />
              </div>
              <p
                style={{
                  fontSize: 10,
                  color: "var(--syn-text-muted)",
                  margin: "2px 0 0",
                  lineHeight: 1.4,
                }}
              >
                {t("graph.filter.spacingHelp")}
              </p>

              {/* ── 4. Node Types ── */}
              <div
                style={{
                  fontSize: 10,
                  color: "var(--syn-text-muted)",
                  letterSpacing: "0.06em",
                  fontWeight: 600,
                  marginTop: 8,
                  marginBottom: 5,
                }}
              >
                {t("graph.header.filterNodeTypes")}
              </div>
              {ALL_NODE_TYPES.map((type) => {
                const checked = filterNodeTypes.size === 0 || filterNodeTypes.has(type);
                const count = nodes.filter((n) => (n.type ?? "other") === type).length;
                if (count === 0) return null;
                const color = TYPE_COLORS[type] ?? DEFAULT_NODE_COLOR;
                return (
                  <label
                    key={type}
                    data-testid={`graph-filter-type-${type}`}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 6,
                      padding: "3px 2px",
                      cursor: "pointer",
                      borderRadius: 3,
                      fontSize: 11,
                      color: "var(--syn-text)",
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleFilterNodeType(type)}
                      style={{ width: 12, height: 12, cursor: "pointer", flexShrink: 0 }}
                    />
                    <span
                      style={{
                        width: 8,
                        height: 8,
                        borderRadius: "50%",
                        background: color,
                        flexShrink: 0,
                      }}
                      aria-hidden="true"
                    />
                    <span style={{ flex: 1, textTransform: "capitalize" }}>{type}</span>
                    <span style={{ fontSize: 10, color: "var(--syn-text-dim)", flexShrink: 0 }}>
                      {count}
                    </span>
                  </label>
                );
              })}

              {/* ── 5. Summary + Reset ── */}
              <div
                data-testid="graph-filter-summary"
                style={{
                  marginTop: 8,
                  fontSize: 10,
                  color: "var(--syn-text-muted)",
                  lineHeight: 1.4,
                  borderTop: "1px solid var(--syn-border)",
                  paddingTop: 6,
                }}
              >
                {t("graph.filter.summary", {
                  visibleNodes,
                  totalNodes: displayTotalNodes,
                  visibleEdges,
                  totalEdges: totalEdgesCount,
                })}
              </div>
              {hasActiveFilter && (
                <button
                  type="button"
                  onClick={() => {
                    onClearAllFilters();
                    setFilterOpen(false);
                  }}
                  style={{
                    marginTop: 6,
                    fontSize: 11,
                    padding: "3px 8px",
                    border: "1px solid var(--syn-border)",
                    borderRadius: 3,
                    background: "none",
                    color: "var(--syn-text-muted)",
                    cursor: "pointer",
                    width: "100%",
                  }}
                >
                  {t("graph.header.clearFilter")}
                </button>
              )}
            </div>
          )}
        </div>

        {/* GR4: Reset — icon + text label (G1 v1.3.14 parity) */}
        <button
          type="button"
          data-testid="graph-header-reset"
          onClick={onReset}
          aria-label={t("graph.header.reset")}
          title={t("graph.header.reset")}
          style={{ ...iconBtnStyle(), width: "auto", padding: "0 6px", gap: 4 }}
        >
          <RotateCcw size={13} strokeWidth={1.8} aria-hidden="true" />
          <span style={{ fontSize: 11, whiteSpace: "nowrap" }}>{t("graph.header.reset")}</span>
        </button>

        {/* Color mode: Type (Tag icon + text label, G1 v1.3.14 parity) */}
        <button
          type="button"
          data-testid="color-mode-type"
          onClick={() => onSetColorMode("type")}
          aria-pressed={colorMode === "type"}
          aria-label={t("graph.colorModeType")}
          title={t("graph.colorModeType")}
          style={{ ...iconBtnStyle(colorMode === "type"), width: "auto", padding: "0 6px", gap: 4 }}
        >
          <Tag size={13} strokeWidth={1.8} aria-hidden="true" />
          <span style={{ fontSize: 11, whiteSpace: "nowrap" }}>{t("graph.colorModeType")}</span>
        </button>

        {/* Color mode: Community (Layers icon + text label, G1 v1.3.14 parity) */}
        <button
          type="button"
          data-testid="color-mode-community"
          onClick={() => onSetColorMode("community")}
          aria-pressed={colorMode === "community"}
          aria-label={t("graph.colorModeCommunity")}
          title={t("graph.colorModeCommunity")}
          style={{
            ...iconBtnStyle(colorMode === "community"),
            width: "auto",
            padding: "0 6px",
            gap: 4,
          }}
        >
          <Layers size={13} strokeWidth={1.8} aria-hidden="true" />
          <span style={{ fontSize: 11, whiteSpace: "nowrap" }}>
            {t("graph.colorModeCommunity")}
          </span>
        </button>

        {/* Insights toggle — Lightbulb + text label + count badge (G1 v1.3.14 parity).
            Label reads "Insights" with inline count when > 0: "Insights 13" style.
            NOTE (deliberate Synapse improvement): insightCount is NOT computed on load —
            it's populated lazily when the panel opens (I3: avoids main-thread work at load time). */}
        {nodes.length > 0 && (
          <button
            type="button"
            data-testid="graph-insights-toggle"
            onClick={onToggleInsights}
            aria-pressed={showInsights}
            aria-label={t("graph.insightsButton")}
            title={t("graph.insightsButton")}
            style={{
              ...iconBtnStyle(showInsights),
              width: "auto",
              padding: "0 6px",
              gap: 4,
              display: "flex",
              alignItems: "center",
            }}
          >
            <Lightbulb size={13} strokeWidth={1.8} aria-hidden="true" />
            <span style={{ fontSize: 11, whiteSpace: "nowrap" }}>{t("graph.insightsButton")}</span>
            {insightCount > 0 && (
              <span
                style={{
                  fontSize: 10,
                  lineHeight: 1.4,
                  minWidth: 14,
                  textAlign: "center",
                  borderRadius: 8,
                  padding: "0 4px",
                  background: showInsights
                    ? "color-mix(in srgb, var(--syn-accent) 20%, transparent)"
                    : "color-mix(in srgb, var(--syn-border) 50%, transparent)",
                  color: showInsights ? "var(--syn-accent)" : "var(--syn-text-muted)",
                }}
              >
                {insightCount}
              </span>
            )}
          </button>
        )}

        {/* Optional regen status message (brief; auto-cleared by parent) */}
        {regenMsg !== null && (
          <span
            style={{
              fontSize: 10,
              color: "var(--syn-text-muted)",
              maxWidth: 110,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
            role="status"
            aria-live="polite"
            data-testid="graph-regenerate-msg"
          >
            {regenMsg}
          </span>
        )}

        {/* Refresh / Regenerate — RefreshCw icon (spins while regenerating) */}
        <button
          type="button"
          data-testid="graph-regenerate"
          onClick={onRefresh}
          disabled={regenerating}
          aria-label={t("graph.regenerate")}
          title={t("graph.regenerateTitle")}
          style={iconBtnStyle(false, regenerating)}
        >
          <RefreshCw
            size={13}
            strokeWidth={1.8}
            aria-hidden="true"
            style={regenerating ? { animation: "syn-spin 0.9s linear infinite" } : undefined}
          />
        </button>

        {/* GR7: Fullscreen */}
        <button
          type="button"
          data-testid="graph-header-fullscreen"
          onClick={onFullscreen}
          aria-label={t("graph.header.fullscreen")}
          title={t("graph.header.fullscreen")}
          style={iconBtnStyle()}
        >
          <Maximize size={13} strokeWidth={1.8} aria-hidden="true" />
        </button>
      </div>
    </div>
  );
};

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
}

const GraphLegend: React.FC<GraphLegendProps> = ({
  colorMode,
  communities,
  onCommunityClick,
  nodes = [],
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
        color: colorForCommunity(c.id),
        lowCohesion: c.cohesion < LOW_COHESION_THRESHOLD,
      }));
  }, [colorMode, communities, t]);

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
            {/* TYPE mode legend — CVD-safe: name + swatch (WCAG 1.4.1) */}
            {Object.entries(TYPE_COLORS).map(([type, color]) => (
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
                        style={{ color: "var(--syn-amber, #d97706)", fontSize: 10, lineHeight: 1 }}
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

// ─── CentroidOverlay ──────────────────────────────────────────────────────────
// Generalised centroid-label overlay for both Community and Domain color modes.
//
// Architecture:
//   - Centroids are received as a pre-memoized Map<string, CommunityCentroid>.
//     (Community mode converts number ids → strings; Domain mode uses domain names.)
//   - On sigma `afterRender`, project graph-space centroids → viewport pixels with
//     sigma.graphToViewport(). Schedule with requestAnimationFrame so DOM writes are
//     batched with sigma's frame (I3: no string work per frame — only numeric transforms).
//   - Labels are positioned absolutely in the overlay div that sits above the canvas.
//   - pointer-events: none so the overlay never blocks sigma interaction.
//   - Hidden entirely when active=false or centroids map is empty.
//
// INVARIANT I2: we NEVER mutate node x/y. We read coords to project them.
// INVARIANT I3: centroid computation is outside this component (memoized by caller).
//              Projection (graphToViewport) is fast: pure arithmetic, no string ops.

interface CentroidOverlayProps {
  /**
   * Pre-memoized centroids keyed by string (community id as string, or domain name).
   * Memoized by GraphViewer caller.
   */
  centroids: Map<string, CommunityCentroid>;
  /** The sigma instance — used to subscribe to afterRender + call graphToViewport. */
  sigmaRef: React.RefObject<Sigma<Attributes, Attributes, Attributes> | null>;
  /** Only render when this mode is active. */
  active: boolean;
  /** data-testid prefix for the overlay container. */
  testId?: string;
}

/** Maximum label character count before truncation with ellipsis. */
const OVERLAY_LABEL_MAX_CHARS = 20;

/**
 * P3: Minimum screen padding (px) so centroid labels never render behind
 * the toolbar/header or outside the canvas bounds.
 * Only the on-screen CSS position is clamped — graph coordinates are untouched (I2).
 */
const CENTROID_LABEL_PAD = 8;

function truncateOverlayLabel(label: string): string {
  if (label.length <= OVERLAY_LABEL_MAX_CHARS) return label;
  return label.slice(0, OVERLAY_LABEL_MAX_CHARS - 1) + "…";
}

const CentroidOverlay: React.FC<CentroidOverlayProps> = ({
  centroids,
  sigmaRef,
  active,
  testId = "community-overlay",
}) => {
  // Stored in a ref (not state) to avoid React re-renders on every sigma frame.
  // We update the DOM directly via the overlayRef to stay off the React tree entirely.
  const overlayRef = useRef<HTMLDivElement>(null);
  // rafHandle: rAF id so we can cancel on cleanup
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    if (!active || centroids.size === 0) {
      // Hide all labels when not in the active mode or no centroids
      if (overlayRef.current) overlayRef.current.style.display = "none";
      return;
    }
    if (overlayRef.current) overlayRef.current.style.display = "block";

    const sigma = sigmaRef.current;
    if (!sigma) return;

    // Project all centroids and update the overlay DOM directly (no React state mutation).
    // This avoids React re-renders on every sigma frame (I3).
    function project() {
      const overlay = overlayRef.current;
      const s = sigmaRef.current;
      if (!overlay || !s) return;

      // Canvas bounds — used to clamp label positions so they stay inside.
      const containerRect = overlay.getBoundingClientRect();
      const maxX = containerRect.width - CENTROID_LABEL_PAD;
      const maxY = containerRect.height - CENTROID_LABEL_PAD;

      // One pass: update each label element's transform. Elements are keyed by data-cid.
      // P3: clamp projected viewport (x, y) so labels never escape the canvas or
      // render behind the toolbar. ONLY the on-screen CSS position is clamped —
      // centroid.x / centroid.y (graph coords from server) are never mutated (I2).
      //
      // De-overlap: in some camera states many centroids project to nearly the SAME
      // point (e.g. a dense cluster panned into a corner), stacking every community
      // label into one illegible "ghost" of text over the toolbar. We greedily place
      // labels in Map order (largest community first) and HIDE any that would land on
      // top of one already placed — so a stack collapses to a single readable label.
      const placed: Array<[number, number]> = [];
      const MIN_SEP = 22; // px; labels closer than this collapse to the first-placed one
      for (const [cid, centroid] of centroids) {
        const el = overlay.querySelector<HTMLElement>(`[data-cid="${String(cid)}"]`);
        if (!el) continue;
        const vp = s.graphToViewport({ x: centroid.x, y: centroid.y });
        const cx = Math.max(CENTROID_LABEL_PAD, Math.min(maxX, vp.x));
        const cy = Math.max(CENTROID_LABEL_PAD, Math.min(maxY, vp.y));
        // If clamping had to MOVE the label, its centroid lies OUTSIDE the on-canvas
        // safe zone ([PAD, max]) — HIDE it instead of stranding it at the edge.
        if (cx !== vp.x || cy !== vp.y) {
          el.style.display = "none";
          continue;
        }
        // Collapse near-coincident labels (declutter + kill the corner pile-up).
        let collides = false;
        for (const [px, py] of placed) {
          if (Math.abs(px - cx) < MIN_SEP && Math.abs(py - cy) < MIN_SEP) {
            collides = true;
            break;
          }
        }
        if (collides) {
          el.style.display = "none";
          continue;
        }
        placed.push([cx, cy]);
        el.style.display = "";
        // Translate so the label is centered on the centroid's viewport position.
        el.style.transform = `translate(calc(${cx}px - 50%), calc(${cy}px - 50%))`;
      }
    }

    // Throttle via rAF: sigma fires afterRender at most 60fps; we just schedule
    // one DOM-write frame after each render event (I3: no per-token string work).
    function onAfterRender() {
      if (rafRef.current !== null) return; // already queued
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = null;
        project();
      });
    }

    // Initial projection
    project();

    // Subscribe to sigma afterRender so projections follow camera moves/zooms
    sigma.on("afterRender", onAfterRender);

    return () => {
      sigma.off("afterRender", onAfterRender);
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
    // Re-subscribe when sigma instance changes (graph rebuild) or centroids change
  }, [active, centroids, sigmaRef]);

  if (!active || centroids.size === 0) return null;

  return (
    <div
      ref={overlayRef}
      data-testid={testId}
      style={{
        position: "absolute",
        inset: 0,
        // pointer-events none: overlay is purely visual — never blocks sigma interaction
        pointerEvents: "none",
        // overflow hidden: labels near edges clip cleanly
        overflow: "hidden",
      }}
      aria-hidden="true"
    >
      {/* Render one label element per centroid.
          Their CSS transform is updated directly in the effect above — NO React state.
          I2: we do not add graph nodes here; this is pure DOM overlay. */}
      {Array.from(centroids.entries()).map(([cid, centroid]) => (
        <div
          key={String(cid)}
          data-cid={String(cid)}
          data-testid={`community-overlay-label-${String(cid)}`}
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            // Hidden until project() positions it. Otherwise, before sigma is ready
            // (or right after a React re-render resets this inline style), every label
            // sits at the container origin (0,0) and they pile into an illegible blob
            // over the top-left toolbar. project() flips display back to "" once it has
            // a real, de-overlapped on-canvas position.
            display: "none",
            // transform is set dynamically in the effect
            transform: "translate(-50%, -50%)",
            fontSize: 11,
            fontWeight: 600,
            fontFamily: "Inter, system-ui, sans-serif",
            letterSpacing: "0.02em",
            color: centroid.color,
            // Text halo for legibility on any background (matches sigma's own halo approach)
            textShadow: [
              "-1px -1px 0 rgba(255,255,255,0.85)",
              " 1px -1px 0 rgba(255,255,255,0.85)",
              "-1px  1px 0 rgba(255,255,255,0.85)",
              " 1px  1px 0 rgba(255,255,255,0.85)",
              " 0    0   3px rgba(255,255,255,0.6)",
            ].join(","),
            whiteSpace: "nowrap",
            userSelect: "none",
          }}
        >
          {truncateOverlayLabel(centroid.label)}
        </div>
      ))}
    </div>
  );
};

// CommunityOverlay removed: "community" colorMode now uses domain grouping (colorForDomain).
// The CentroidOverlay component handles domain centroids directly with string-keyed Map.

// ─── Status bar ───────────────────────────────────────────────────────────────

const StatusBar: React.FC = () => {
  const { t } = useTranslation();
  const { loading, error } = useGraphStatus();
  const { dataVersion, cacheStatus } = useGraphMeta();
  const nodes = useGraphStore(selectNodes);
  const edges = useGraphStore(selectEdges);

  if (error !== null) {
    return (
      <div
        style={{
          position: "absolute",
          top: 12,
          left: "50%",
          transform: "translateX(-50%)",
          background: "var(--syn-red)",
          color: "#fff",
          borderRadius: "var(--syn-radius-sm)",
          padding: "4px 12px",
          fontSize: 12,
          zIndex: 10,
        }}
        role="alert"
      >
        {error}
      </div>
    );
  }

  if (loading) {
    return (
      <div
        className="syn-card"
        style={{
          position: "absolute",
          top: 12,
          left: "50%",
          transform: "translateX(-50%)",
          padding: "4px 12px",
          fontSize: 12,
          color: "var(--syn-text-muted)",
          zIndex: 10,
        }}
        aria-live="polite"
        aria-busy="true"
      >
        {t("graph.loading")}
      </div>
    );
  }

  return (
    <div
      className="syn-card"
      style={{
        position: "absolute",
        bottom: 12,
        left: "50%",
        transform: "translateX(-50%)",
        padding: "3px 8px",
        fontSize: 11,
        color: "var(--syn-text-muted)",
        zIndex: 5,
        display: "flex",
        gap: 10,
        userSelect: "none",
      }}
      aria-label="Graph statistics"
    >
      <span>{nodes.length} nodes</span>
      <span>{edges.length} edges</span>
      {dataVersion !== null && <span>v{dataVersion}</span>}
      {cacheStatus !== "unknown" && (
        <span style={{ color: cacheStatus === "hit" ? "var(--syn-green)" : "var(--syn-amber)" }}>
          {cacheStatus}
        </span>
      )}
    </div>
  );
};

// ─── Hover-dim state (closed-over, NOT React state — no re-render on hover) ────

interface HoverState {
  hoveredNode: string | null;
  hoveredNeighbors: Set<string> | null;
  selectedNode: string | null;
}

// ─── Drag state (closed-over, NOT React state — no re-render during drag) ─────
//
// INVARIANT I2 note: drag moves ONLY the ONE dragged node by writing its x/y
// directly onto the graphology graph. No layout algorithm is invoked. No other
// node is moved. No rAF physics loop is started. The final position is persisted
// to the server via PATCH /pages/{id}/position so it survives the next GET /graph.

interface DragState {
  /** Node currently being dragged, or null */
  draggedNode: string | null;
  /** True once the pointer has moved enough to count as a drag (not a click) */
  hasMoved: boolean;
  /** Viewport-space pointer position at drag start (for the movement threshold) */
  downX: number;
  downY: number;
}

/**
 * Minimum viewport-pixel movement before a pointer-down on a node is treated as a
 * drag (and thus persisted + pinned). Below this, it is a click/tap. This stops a
 * mobile tap with slight touch jitter from accidentally pinning a node at its current
 * (possibly runaway) coordinates — the root cause of the "graph collapsed to the
 * center" bug (the server-side outlier clamp is the safety net; this is prevention).
 */
const DRAG_THRESHOLD_PX = 5;

// ─── Main GraphViewer ──────────────────────────────────────────────────────────

interface TooltipState {
  nodeId: string;
  position: { x: number; y: number };
  neighborCount: number;
}

/**
 * GraphViewer — Obsidian-style sigma.js WebGL knowledge-graph viewer.
 *
 * DOM structure (I4 — <20 nodes in container):
 *   <div#graph-root role="application">  ← container
 *     <div#sigma-container>              ← sigma mounts its WebGL canvas here
 *       <canvas>                         ← ONE WebGL canvas (sigma owns this)
 *     </div>
 *     <div aria-live="polite">           ← accessible selection announcements
 *     <StatusBar />                      ← absolute overlay
 *     <GraphLegend />                    ← absolute overlay
 *     <NodeTooltip />                    ← conditional, single element
 *   </div>
 */
export const GraphViewer: React.FC = () => {
  const { t } = useTranslation();
  // I3: typed selectors — never subscribe to whole store
  const nodes = useGraphStore(selectNodes);
  const edges = useGraphStore(selectEdges);
  const communities = useGraphStore(selectCommunities);
  const vaultId = useGraphStore(selectVaultId);
  const selectedNodeId = useGraphStore(selectSelectedNodeId);
  const setGraph = useGraphStore(selectSetGraph);
  const setLoading = useGraphStore(selectSetLoading);
  const setError = useGraphStore(selectSetError);
  const setSelectedNodeId = useGraphStore(selectSetSelectedNodeId);
  // GR3: node-type filter from store (I2-safe: visibility only, never re-layout)
  // Use a ref so the sigma reducers always read the latest filter without rebuilding sigma.
  const filterNodeTypes = useGraphStore(selectFilterNodeTypes);
  const filterNodeTypesRef = useRef<Set<string>>(filterNodeTypes);
  // GI-2 (v1.3.14): extended filter state from store — declared BEFORE the refs below
  // that capture them, to avoid a temporal-dead-zone (the refs' initial useRef(...) reads
  // these values). Live-preview caught this ordering bug in the initial GI-2 draft.
  const hideMetaTypes = useGraphStore(selectHideMetaTypes);
  const hideIsolated = useGraphStore(selectHideIsolated);
  const minLinks = useGraphStore(selectMinLinks);
  const maxLinks = useGraphStore(selectMaxLinks);
  const nodeSizeScale = useGraphStore(selectNodeSizeScale);
  const spacingScale = useGraphStore(selectSpacingScale);
  // GI-2: refs for extended filter state so sigma reducers always read latest values
  // without rebuilding sigma on every filter change (I3: no re-render per frame).
  const hideMetaTypesRef = useRef<boolean>(hideMetaTypes);
  const hideIsolatedRef = useRef<boolean>(hideIsolated);
  const minLinksRef = useRef<number | null>(minLinks);
  const maxLinksRef = useRef<number | null>(maxLinks);
  const nodeSizeScaleRef = useRef<number>(nodeSizeScale);
  // Persistent selection: nodeReducer reads this ref so the clicked node keeps a
  // ring + label at rest (not just on hover). Ref (not state) so the reducer sees
  // the latest value without rebuilding sigma — same pattern as filterNodeTypesRef.
  const selectedNodeIdRef = useRef<string | null>(null);
  const toggleFilterNodeType = useGraphStore(selectToggleFilterNodeType);
  const clearFilterNodeTypes = useGraphStore(selectClearFilterNodeTypes);
  const setHideMetaTypes = useGraphStore(selectSetHideMetaTypes);
  const setHideIsolated = useGraphStore(selectSetHideIsolated);
  const setMinLinks = useGraphStore(selectSetMinLinks);
  const setMaxLinks = useGraphStore(selectSetMaxLinks);
  const setNodeSizeScale = useGraphStore(selectSetNodeSizeScale);
  const setSpacingScale = useGraphStore(selectSetSpacingScale);
  const clearAllGraphFilters = useGraphStore(selectClearAllGraphFilters);
  // GR1: total vault pages from backend (null = old server)
  const totalNodes = useGraphStore(selectTotalNodes);
  // GR2: selectPage action for search-triggered navigation
  const selectPage = useGraphStore(selectSelectPage);
  // Click-to-open: navigate to the wiki pages section for the clicked node (Obsidian-style).
  const setActiveSection = useGraphStore(selectSetActiveSection);

  // WS-A [F4/F16]: subscribe to data_version from the ActivityBar's existing GET /status poll.
  // When the version bumps, we re-fetch GET /graph (precomputed coords from server — I2).
  // INVARIANT I3: no re-render on every poll tick; only triggers when the value changes.
  // INVARIANT I2: we NEVER run a layout algorithm — we only refetch server-computed coords.
  // INVARIANT AC-WS-A-4: no new poller; ActivityBar's STATUS_POLL_MS is the sole driver.
  const statusDataVersion = useStatusStore(selectStatusDataVersion);

  // Track which data_version the current graph data corresponds to so we only
  // refetch when the server version actually advances (AC-WS-A-3).
  const lastFetchedGraphVersionRef = useRef<number | null>(null);

  // Graph container ref — used for fullscreen API (GR7)
  const graphRootRef = useRef<HTMLDivElement>(null);

  // sigma container ref — sigma mounts ONE WebGL <canvas> inside this div (I4)
  const containerRef = useRef<HTMLDivElement>(null);
  // sigma instance ref — kept outside React state to avoid re-render on mount
  const sigmaRef = useRef<Sigma<Attributes, Attributes, Attributes> | null>(null);

  // Resolved sigma theme colors — updated on mount and on theme change (ADR-0048 §T1)
  // React state drives re-render so sigma can be re-instantiated with correct colors.
  const [sigmaThemeColors, setSigmaThemeColors] = React.useState<SigmaThemeColors>(() =>
    readSigmaThemeColors(),
  );

  // Color-mode toggle: "community" (default — colors by Louvain community id, one distinct
  // color per cluster from COMMUNITY_PALETTE) or "type" (colors by page type).
  // Community names in legend + centroid overlay use communityDisplayName(c) which forms
  // "{dominant_domain} · {top_page_subtopic}" — unique per cluster, no duplicate SAM rows.
  const [colorMode, setColorMode] = useState<ColorMode>("type");

  // Tooltip state (React state — triggers re-render to show/hide tooltip)
  const [tooltip, setTooltip] = useState<TooltipState | null>(null);
  // Aria-live announcement text
  const [announcement, setAnnouncement] = useState<string>("");

  // ── Community centroid map (I3 — memoized; recomputed only when nodes/communities change)
  // Active in "community" color mode. Groups nodes by Louvain community id (server-provided).
  // Labels come from communityDisplayName(c) — unique "{domain} · {subtopic}" names.
  // INVARIANT I2: only reads server-provided x/y and community field — never mutates coords.
  // INVARIANT I3: computed once per nodes/communities change, NOT per sigma frame.
  const communityCentroids = useMemo(() => {
    // Convert number-keyed Map<number, CommunityCentroid> → string-keyed Map<string, CommunityCentroid>
    // because CentroidOverlay uses string keys (supports both community and domain modes).
    const raw = computeCommunityCentroids(nodes, communities);
    const result = new Map<string, CommunityCentroid>();
    for (const [cid, centroid] of raw) {
      result.set(String(cid), centroid);
    }
    return result;
  }, [nodes, communities]);

  // ── R9-5: Community panel state ──────────────────────────────────────────
  const [communityPanel, setCommunityPanel] = useState<{ id: number; color: string } | null>(null);

  // ── R9-5: Edge breakdown tooltip state ──────────────────────────────────
  interface EdgeTooltipState {
    srcId: string;
    tgtId: string;
    position: { x: number; y: number };
  }
  const [edgeTooltip, setEdgeTooltip] = useState<EdgeTooltipState | null>(null);
  // Per-pair cache: key = "srcId__tgtId" or "tgtId__srcId"
  const edgeDetailCache = useRef<Map<string, EdgeDetail>>(new Map());

  const handleEdgeCached = useCallback((key: string, detail: EdgeDetail) => {
    edgeDetailCache.current.set(key, detail);
  }, []);

  // Regenerate-graph control state (reresolve links + recompute FA2, then refetch coords)
  const [regenerating, setRegenerating] = useState(false);
  const [regenMsg, setRegenMsg] = useState<string | null>(null);

  // ── Regenerate graph: reconnect cross-ingest links → server recomputes FA2 → refetch ──
  const handleRegenerate = useCallback(async () => {
    if (regenerating) return;
    setRegenerating(true);
    setRegenMsg(null);
    try {
      // 1. Reconnect cross-ingest links + FORCE a fresh server-side FA2 recompute (I2).
      //    Forcing (not just reresolve) guarantees the layout re-runs — so the outlier
      //    clamp takes effect and the graph stops collapsing to a dot.
      const result = await recomputeGraph();
      // 2. Refetch the freshly-computed precomputed coords (I2 — layout stays server-side).
      const { data, cacheStatus } = await fetchGraph(vaultId);
      setGraph(
        data.nodes,
        data.edges,
        data.data_version,
        cacheStatus,
        data.communities ?? [],
        data.total_nodes ?? null,
        data.total_edges ?? null,
      );
      setRegenMsg(
        result.reconnected > 0
          ? t("graph.regenerateDone", { count: result.reconnected })
          : t("graph.regenerateNone"),
      );
    } catch (err: unknown) {
      setRegenMsg(t("graph.regenerateError"));
      if (err instanceof Error && err.name !== "AbortError") {
        setError(err.message);
      }
    } finally {
      setRegenerating(false);
    }
  }, [regenerating, vaultId, setGraph, setError, t]);

  // ── Fetch graph on mount / vaultId change ────────────────────────────────
  //
  // P2 — skip redundant fetch when the Zustand store already holds current data.
  //
  // On a REVISIT (navigate away → navigate back), the component unmounts/remounts
  // but the graphStore retains its nodes + dataVersion from the previous fetch.
  // If the store's dataVersion matches the latest statusDataVersion (from the
  // ActivityBar's existing /status poll), the data is already current and we can
  // rebuild sigma directly from the store without a network round-trip.
  //
  // Guards:
  //  - storeNodes.length > 0        : store actually has data (not first load)
  //  - storeDataVersion !== null     : store knows its version
  //  - currentStatusVersion !== null : status store has polled at least once
  //  - versions match                : data is confirmed current
  //
  // Read store state imperatively via .getState() — this avoids adding reactive
  // deps to the effect and keeps I3 clean (no extra subscriptions).
  //
  // INVARIANT I2: no layout algorithm invoked — sigma rebuilds from precomputed
  // server coords stored in the Zustand nodes array (unchanged).

  useEffect(() => {
    // P2: cache-hit check — skip fetch when store data is already at the current version.
    const { nodes: storeNodes, dataVersion: storeDataVersion } = useGraphStore.getState();
    const currentStatusVersion = useStatusStore.getState().dataVersion;

    if (
      storeNodes.length > 0 &&
      storeDataVersion !== null &&
      currentStatusVersion !== null &&
      storeDataVersion === currentStatusVersion
    ) {
      // Store data is current. Sigma will rebuild from the existing nodes array.
      // Initialise the WS-A ref so a same-version status tick doesn't trigger a
      // redundant re-fetch via the WS-A effect below (AC-WS-A-3).
      lastFetchedGraphVersionRef.current = storeDataVersion;
      return; // no AbortController cleanup needed
    }

    const ctrl = new AbortController();
    setLoading(true);

    fetchGraph(vaultId, ctrl.signal)
      .then(({ data, cacheStatus }) => {
        setGraph(
          data.nodes,
          data.edges,
          data.data_version,
          cacheStatus,
          data.communities ?? [],
          data.total_nodes ?? null,
          data.total_edges ?? null,
        );
        // WS-A: record the server version just fetched so we don't re-fetch on same-version ticks.
        lastFetchedGraphVersionRef.current = data.data_version;
      })
      .catch((err: unknown) => {
        if (err instanceof Error && err.name !== "AbortError") {
          setError(err.message);
        }
      });

    return () => ctrl.abort();
  }, [vaultId, setGraph, setLoading, setError]);

  // ── WS-A [AC-WS-A-2, AC-WS-A-3]: re-fetch graph when data_version bumps ──
  // Polls via the existing ActivityBar /status cadence — no new interval (AC-WS-A-4).
  // Skips re-fetch if the version hasn't changed from last graph fetch (AC-WS-A-3).
  // INVARIANT I2: only calls fetchGraph; NEVER runs FA2 or any layout algorithm.
  // INVARIANT I3: effect deps are the version scalar; no per-tick re-render when unchanged.
  useEffect(() => {
    if (statusDataVersion === null) return;
    if (statusDataVersion === lastFetchedGraphVersionRef.current) return;
    // Version has advanced — refetch precomputed coords from server (AC-WS-A-2).
    const ctrl = new AbortController();
    fetchGraph(vaultId, ctrl.signal)
      .then(({ data, cacheStatus }) => {
        setGraph(
          data.nodes,
          data.edges,
          data.data_version,
          cacheStatus,
          data.communities ?? [],
          data.total_nodes ?? null,
          data.total_edges ?? null,
        );
        lastFetchedGraphVersionRef.current = data.data_version;
      })
      .catch((err: unknown) => {
        // Transient errors (network hiccup) — don't surface to the user, just log.
        if (err instanceof Error && err.name !== "AbortError") {
          console.warn("[WS-A] graph freshness re-fetch failed:", err.message);
        }
      });
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusDataVersion]); // vaultId, setGraph intentionally omitted: mount effect owns initial fetch

  // ── GR3: sync filterNodeTypes ref and refresh sigma on filter change ─────
  // The ref lets the existing sigma reducers always see the latest filter value
  // without tearing down and rebuilding sigma on every toggle (I3: no heavy
  // work per frame; I2: no coords touched, only sigma's hidden flag is changed).
  useEffect(() => {
    filterNodeTypesRef.current = filterNodeTypes;
    // Trigger a visual refresh so sigma re-evaluates nodeReducer/edgeReducer
    // with the updated filter. skipIndexation: layout is not touched (I2).
    sigmaRef.current?.refresh({ skipIndexation: true });
  }, [filterNodeTypes]);

  // ── GI-2: sync filter refs and trigger visual refresh when visibility filters change ──
  // I2-safe: only sets hidden flags in reducers; never touches node coordinates.
  // I3-safe: updates refs (not state) so no re-render is triggered; sigma.refresh once.
  useEffect(() => {
    hideMetaTypesRef.current = hideMetaTypes;
    hideIsolatedRef.current = hideIsolated;
    minLinksRef.current = minLinks;
    maxLinksRef.current = maxLinks;
    sigmaRef.current?.refresh({ skipIndexation: true });
  }, [hideMetaTypes, hideIsolated, minLinks, maxLinks]);

  // ── GI-2: node size scale — visual multiplier applied in nodeReducer via ref ───────
  useEffect(() => {
    nodeSizeScaleRef.current = nodeSizeScale;
    sigmaRef.current?.refresh({ skipIndexation: true });
  }, [nodeSizeScale]);

  // ── GI-2: spacing scale — translate sigma node positions around the centroid ────────
  // Uses original `nodes` from store as source of truth for positions (I2: precomputed
  // by server; pure arithmetic scale around centroid — no FA2, no force iteration).
  // skipIndexation:false so sigma re-indexes the rescaled positions into camera space.
  useEffect(() => {
    const sigma = sigmaRef.current;
    if (!sigma || nodes.length === 0) return;
    const sigmaGraph = sigma.getGraph();
    if (sigmaGraph.order === 0) return;

    // Build O(n) position lookup from server-side positions (never mutated by client)
    let sumX = 0;
    let sumY = 0;
    const origPos = new Map<string, { x: number; y: number }>();
    for (const n of nodes) {
      origPos.set(n.id, { x: n.x, y: n.y });
      sumX += n.x;
      sumY += n.y;
    }
    const cx = sumX / nodes.length;
    const cy = sumY / nodes.length;

    // Scale each node position around the centroid (pure arithmetic — I2)
    sigmaGraph.forEachNode((nodeKey) => {
      const orig = origPos.get(nodeKey);
      if (!orig) return;
      sigmaGraph.setNodeAttribute(nodeKey, "x", cx + (orig.x - cx) * spacingScale);
      sigmaGraph.setNodeAttribute(nodeKey, "y", cy + (orig.y - cy) * spacingScale);
    });

    sigma.refresh({ skipIndexation: false });
  }, [spacingScale, nodes]);

  // ── Watch resolved theme changes, re-read sigma render properties (ADR-0048 §T1) ──
  // Observes data-theme on <html>; on change, reads the new CSS vars and updates
  // sigmaThemeColors, which is in the sigma-mount effect deps → sigma rebuilds with
  // the new colors. This is a render-property update ONLY — no layout/coords touched (I2).

  useEffect(() => {
    const observer = new MutationObserver(() => {
      setSigmaThemeColors(readSigmaThemeColors());
    });
    observer.observe(document.documentElement, { attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, []);

  // ── Build graphology graph and mount sigma ────────────────────────────────

  useEffect(() => {
    if (!containerRef.current) return;
    if (nodes.length === 0) return;

    // Build graphology graph from precomputed coords.
    // I2: buildGraphologyGraph sets x/y directly from server — no layout called.
    // Theme drives the resting edge ramps: light-theme grays glare on the dark canvas.
    // sigmaThemeColors is in this effect's deps, so a theme switch rebuilds with the
    // right ramps (data-theme is set by the same code path that changes --syn-bg).
    const isDarkTheme = document.documentElement.getAttribute("data-theme") === "dark";
    const rawGraph = buildGraphologyGraph(nodes, edges, isDarkTheme ? "dark" : "light");

    // Build a plain Attributes graphology graph for sigma.
    // Copy all node/edge attributes (including server x/y) into the sigma graph.
    const SigmaGraphCtor = rawGraph.constructor as new (opts: {
      multi: boolean;
      type: string;
    }) => import("graphology").default;
    const sigmaGraph = new SigmaGraphCtor({ multi: false, type: "undirected" });

    rawGraph.forEachNode((nodeKey, rawAttrs) => {
      // Cast to Attributes (Record<string,unknown>) so dynamic key access is type-safe.
      const attrs = rawAttrs as Attributes;
      const nodeType = attrs["type"] as string | null | undefined;
      // Community id — -1 when unassigned or from older servers (non-breaking, set in graphTransform)
      const nodeCommunity = (attrs["community"] as number | undefined) ?? -1;
      // Domain name — null when untagged or from older servers (non-breaking).
      // I2: value comes from the server (GraphNode.domain); never computed client-side.
      const nodeDomain = (attrs["domain"] as string | null | undefined) ?? null;
      // Color is determined by active color-mode (I2: all values come from server).
      // "community" mode colors by Louvain community id — one distinct color per cluster
      // from COMMUNITY_PALETTE (cycles for >12 communities; -1 = unassigned → gray).
      // "type" mode colors by page type (concept, entity, source, …).
      const nodeColor =
        colorMode === "community"
          ? colorForCommunity(nodeCommunity)
          : colorForType(nodeType ?? null);
      sigmaGraph.addNode(nodeKey, {
        x: attrs["x"] as number,
        y: attrs["y"] as number,
        label: attrs["label"] as string,
        // GL2: pre-truncated hub label; nodeReducer swaps this in for hub nodes at rest
        hubLabel: (attrs["hubLabel"] as string | undefined) ?? (attrs["label"] as string),
        size: attrs["size"] as number,
        color: nodeColor,
        // Store degree for reducers
        degree: attrs["degree"] as number,
        // Store type for reducers
        nodeType: nodeType ?? null,
        // Store community for reducers / tooltip
        nodeCommunity,
        // Store domain for reducers (I2: from server)
        nodeDomain,
        // GL2: hub flag — nodeReducer uses this to force permanent truncated label on top-K hubs
        isHub: (attrs["forceLabel"] as boolean | undefined) ?? false,
      });
    });

    rawGraph.forEachEdge((_edgeKey, attrs, source, target) => {
      if (!sigmaGraph.hasEdge(source, target)) {
        sigmaGraph.addEdge(source, target, {
          weight: attrs["weight"] as number,
          color: attrs["color"] as string,
          size: attrs["size"] as number,
          // GL1: pass through normalizedWeight so edgeReducer can check it on hover
          normalizedWeight: attrs["normalizedWeight"] as number,
          // GL1: resting hidden flag (weak edges culled at rest, revealed on hover)
          hidden: attrs["hidden"] as boolean,
        });
      }
    });

    // Destroy previous sigma instance before creating a new one
    if (sigmaRef.current) {
      sigmaRef.current.kill();
      sigmaRef.current = null;
    }

    // ── Hover-dim closure state ───────────────────────────────────────────
    // Stored in a plain object (NOT React state) so mutations do NOT cause re-renders.
    // Reducers read from this object; we call renderer.refresh({skipIndexation:true}) manually.
    const hoverState: HoverState = {
      hoveredNode: null,
      hoveredNeighbors: null,
      selectedNode: null,
    };

    // ── sigma v3 settings ────────────────────────────────────────────────
    // nodeReducer / edgeReducer implement Obsidian-style hover-dim.
    // They are set ONCE at construction; we mutate hoverState and call refresh().
    // Theme colors (bg, labelColor, halo) are read from resolved CSS vars (ADR-0048 §T1).
    // This is render-property only — no layout/coords touched (I2).
    const sigmaSettings: Partial<Settings<Attributes, Attributes, Attributes>> = {
      // Label rendering — uses resolved --syn-text for label color
      labelFont: "Inter, system-ui, sans-serif",
      labelSize: 13,
      labelWeight: "600",
      labelColor: { color: sigmaThemeColors.labelColor },
      // Obsidian-style labels: hidden when zoomed out (just dots), fade in as you zoom.
      // Node rendered size scales with camera zoom; a label shows only once a node's
      // on-screen size passes labelRenderedSizeThreshold. With the small node range
      // (2.5–11px) this threshold keeps the fit/zoomed-out view label-free, and labels
      // appear progressively (hubs first) as the user zooms in. labelDensity raised so
      // more labels reveal when eligible.
      // GL2 (B3-LOOK): threshold was lowered from 13→8 for more labels on zoom-in.
      // Declutter pass 2026-07: raised back from 8→11 so fewer non-hub labels crowd
      // the fit-view. Hub nodes still appear at rest via forceLabel regardless.
      labelDensity: 0.7,
      labelGridCellSize: 70,
      labelRenderedSizeThreshold: 11,

      // Parity with llm_wiki 0.6.0: suppress labels/edges while the camera moves so
      // panning/zooming stays legible and light on large graphs (I3). Static hover is
      // unaffected — only the hovered node forces a label (see nodeReducer).
      hideLabelsOnMove: true,
      hideEdgesOnMove: true,

      // Custom halo drawers — built with resolved theme colors (ADR-0048 §T1)
      defaultDrawNodeLabel: makeDrawHaloNodeLabel(sigmaThemeColors),
      defaultDrawNodeHover: makeDrawHaloNodeHover(sigmaThemeColors),

      // Edge events required for edgeReducer hover detection
      enableEdgeEvents: true,

      // The container can legitimately measure 0px for one frame while the
      // flex layout settles (theme-driven re-instantiation, section switch).
      // Without this sigma THROWS at construction ("Container has no width")
      // and — before SectionErrorBoundary existed — unmounted the whole app.
      // The existing ResizeObserver picks up the real size immediately after.
      allowInvalidContainer: true,

      // zIndex enables per-node z ordering in reducers
      zIndex: true,

      // Camera bounds
      minCameraRatio: 0.1,
      maxCameraRatio: 4,

      // ── nodeReducer: GR3 filter + Obsidian hover-dim + GL2 hub labels ───
      nodeReducer(node: string, data: Attributes): Partial<NodeDisplayData> {
        const res: Partial<NodeDisplayData> & Attributes = { ...data };

        // GR3: node-type filter — hide nodes whose type is not in the active set.
        // I2-safe: only sets hidden:true, never touches x/y or re-layout.
        // I3-safe: reads from filterNodeTypesRef (updated via useEffect above)
        //   so no re-render per frame — the ref is mutated and sigma.refresh() called once.
        const activeFilter = filterNodeTypesRef.current;
        if (activeFilter.size > 0) {
          const nodeType = (data["nodeType"] as string | null | undefined) ?? null;
          const typeKey = nodeType ?? "other";
          if (!activeFilter.has(typeKey)) {
            res["hidden"] = true;
            return res as Partial<NodeDisplayData>;
          }
        }

        // GI-2: hide meta-type nodes (index, log, overview) — I2-safe (visibility only).
        if (hideMetaTypesRef.current) {
          const nodeType = (data["nodeType"] as string | null | undefined) ?? "";
          if (META_NODE_TYPES.has(nodeType)) {
            res["hidden"] = true;
            return res as Partial<NodeDisplayData>;
          }
        }

        // GI-2: hide isolated nodes (degree 0) — I2-safe.
        if (hideIsolatedRef.current && ((data["degree"] as number | undefined) ?? 0) === 0) {
          res["hidden"] = true;
          return res as Partial<NodeDisplayData>;
        }

        // GI-2: min/max links filter — I2-safe.
        {
          const nodeDegree = (data["degree"] as number | undefined) ?? 0;
          const minL = minLinksRef.current;
          const maxL = maxLinksRef.current;
          if (minL !== null && nodeDegree < minL) {
            res["hidden"] = true;
            return res as Partial<NodeDisplayData>;
          }
          if (maxL !== null && nodeDegree > maxL) {
            res["hidden"] = true;
            return res as Partial<NodeDisplayData>;
          }
        }

        // GI-2: node size scale (visual multiplier — I2: only changes render size, no coords).
        const sizeScale = nodeSizeScaleRef.current;
        if (sizeScale !== 1.0) {
          res["size"] = ((data["size"] as number | undefined) ?? 8) * sizeScale;
        }

        // GL2: hub nodes always show their truncated label at rest (top-K by degree).
        // hubLabel is pre-computed at build time (truncateHubLabel) — no per-frame work (I3).
        if ((data["isHub"] as boolean | undefined) === true) {
          res["forceLabel"] = true;
          // Replace the full label with the truncated hub label so sigma draws
          // the short version at rest. Full title stays in the tooltip (nodeId → fetchPageDetail).
          res["label"] = (data["hubLabel"] as string | undefined) ?? (data["label"] as string);
        }

        if (hoverState.hoveredNeighbors !== null) {
          const isHovered = node === hoverState.hoveredNode;
          const isNeighbor = hoverState.hoveredNeighbors.has(node);

          if (isHovered) {
            // Hovered node: highlighted, forced label (full title restored), top z, slight size bump
            res["highlighted"] = true;
            res["forceLabel"] = true;
            // Restore the full label on the hovered node so the tooltip confirms the exact title
            res["label"] = data["label"] as string;
            res["zIndex"] = 2;
            res["size"] = ((data["size"] as number | undefined) ?? 8) * 1.15;
          } else if (isNeighbor) {
            // Neighbor: raised z + deepened color so the cluster pops — but do NOT
            // force its label. Matches llm_wiki 0.6.0 (only the hovered node forces a
            // label; neighbours are highlighted, not labelled), so hovering a hub no
            // longer floods the view with every neighbour's title. A neighbour that is
            // itself a hub still shows its truncated label via the isHub branch above.
            res["zIndex"] = 1;
            // Mix toward black to deepen while preserving hue (light-theme pop)
            res["color"] = deepenColor((data["color"] as string | undefined) ?? DEFAULT_NODE_COLOR);
          } else {
            // All other nodes: dim (washed-out, hide label) — use --syn-border resolved value
            res["label"] = "";
            res["color"] =
              sigmaThemeColors.labelColor === "#1f2328"
                ? "#c7ccd4" // light mode: close to --syn-border
                : "#30363d"; // dark mode: close to --syn-border dark
            res["zIndex"] = 0;
          }
        } else if (selectedNodeIdRef.current !== null && node === selectedNodeIdRef.current) {
          // Persistent selection ring at REST (no hover active). Reuses sigma's
          // hover renderer (makeDrawHaloNodeHover) via highlighted:true, so the
          // clicked node keeps a ring + full label until another node is selected.
          // Hover always wins: this branch only runs when hoveredNeighbors === null.
          res["highlighted"] = true;
          res["forceLabel"] = true;
          res["label"] = data["label"] as string;
          res["zIndex"] = 2;
        }

        return res as Partial<NodeDisplayData>;
      },

      // ── edgeReducer: GR3 filter + GI-2 filters + GL1 resting cull + Obsidian hover reveal ─
      // At rest: edges with hidden:true (weak edges per GL1) are not rendered.
      // GR3: edges whose source or target is filtered out are also hidden.
      // GI-2: additional visibility checks for meta-type, isolated, min/max links.
      // On hover: incident edges are ALWAYS revealed regardless of GL1 threshold
      //   so the user can explore the full neighborhood even on large graphs.
      // Non-incident edges during hover: hidden (Obsidian dim).
      // I2-safe: visibility only, no coord mutation.
      edgeReducer(edge: string, data: Attributes) {
        const res: Attributes = { ...data };

        // Extract endpoints once for all filter checks (avoids repeated extremities() calls).
        const [src, tgt] = sigmaGraph.extremities(edge);

        // GR3: hide edge if either endpoint type is filtered out.
        const activeFilter = filterNodeTypesRef.current;
        if (activeFilter.size > 0) {
          const srcType =
            (sigmaGraph.getNodeAttribute(src, "nodeType") as string | null | undefined) ?? null;
          const tgtType =
            (sigmaGraph.getNodeAttribute(tgt, "nodeType") as string | null | undefined) ?? null;
          if (!activeFilter.has(srcType ?? "other") || !activeFilter.has(tgtType ?? "other")) {
            res["hidden"] = true;
            return res;
          }
        }

        // GI-2: hide edge if either endpoint is a meta-type (index/log/overview).
        if (hideMetaTypesRef.current) {
          const srcType =
            (sigmaGraph.getNodeAttribute(src, "nodeType") as string | null | undefined) ?? "";
          const tgtType =
            (sigmaGraph.getNodeAttribute(tgt, "nodeType") as string | null | undefined) ?? "";
          if (META_NODE_TYPES.has(srcType) || META_NODE_TYPES.has(tgtType)) {
            res["hidden"] = true;
            return res;
          }
        }

        // GI-2: hide edge if either endpoint is isolated (degree 0) and hideIsolated is on.
        if (hideIsolatedRef.current) {
          const srcDeg = (sigmaGraph.getNodeAttribute(src, "degree") as number | undefined) ?? 0;
          const tgtDeg = (sigmaGraph.getNodeAttribute(tgt, "degree") as number | undefined) ?? 0;
          if (srcDeg === 0 || tgtDeg === 0) {
            res["hidden"] = true;
            return res;
          }
        }

        // GI-2: hide edge if either endpoint doesn't pass min/max links filter.
        {
          const minL = minLinksRef.current;
          const maxL = maxLinksRef.current;
          if (minL !== null || maxL !== null) {
            const srcDeg = (sigmaGraph.getNodeAttribute(src, "degree") as number | undefined) ?? 0;
            const tgtDeg = (sigmaGraph.getNodeAttribute(tgt, "degree") as number | undefined) ?? 0;
            if (minL !== null && (srcDeg < minL || tgtDeg < minL)) {
              res["hidden"] = true;
              return res;
            }
            if (maxL !== null && (srcDeg > maxL || tgtDeg > maxL)) {
              res["hidden"] = true;
              return res;
            }
          }
        }

        if (hoverState.hoveredNode !== null) {
          const srcRelevant =
            src === hoverState.hoveredNode || (hoverState.hoveredNeighbors?.has(src) ?? false);
          const tgtRelevant =
            tgt === hoverState.hoveredNode || (hoverState.hoveredNeighbors?.has(tgt) ?? false);

          if (!srcRelevant || !tgtRelevant) {
            // Non-incident: hide entirely (Obsidian dim; overrides GL1 reveal)
            res["hidden"] = true;
          } else {
            // GL1 REVEAL: incident edges always shown on hover, even if below threshold.
            res["hidden"] = false;
            // Emphasis color follows the theme — darker on a light canvas,
            // LIGHTER on a dark canvas (a dark emphasis would vanish).
            const dark = document.documentElement.getAttribute("data-theme") === "dark";
            // Active-edge highlight — cyan on dark, slate-800 on light (llm_wiki 0.6.0 parity)
            res["color"] = dark ? "#38bdf8" : "#1e293b";
            res["size"] = ((data["size"] as number | undefined) ?? 1) * 2;
          }
        }
        // At rest (no hover): hidden flag is whatever was set in buildGraphologyGraph (GL1).
        // sigma respects hidden:true by skipping the edge in WebGL draw — no data mutation.

        return res;
      },
    };

    // Instantiate sigma — creates ONE WebGL <canvas> inside containerRef (I4)
    // I2: sigma renders FIXED precomputed positions. No layout algorithm. No rAF physics.
    const sigma = new Sigma(sigmaGraph, containerRef.current, sigmaSettings);
    sigmaRef.current = sigma;

    // ── Drag state (closed-over plain object — no React re-render during drag) ─
    const dragState: DragState = { draggedNode: null, hasMoved: false, downX: 0, downY: 0 };

    // ── Event handlers ────────────────────────────────────────────────────
    // Mutate hoverState / dragState in-place, then call refresh.
    // Do NOT call setSetting inside handlers (spec requirement).

    sigma.on("enterNode", ({ node }) => {
      // Suppress hover-dim while a drag is in progress
      if (dragState.draggedNode !== null) return;
      hoverState.hoveredNode = node;
      hoverState.hoveredNeighbors = new Set(sigmaGraph.neighbors(node));
      sigma.refresh({ skipIndexation: true });
    });

    sigma.on("leaveNode", () => {
      if (dragState.draggedNode !== null) return;
      hoverState.hoveredNode = null;
      hoverState.hoveredNeighbors = null;
      sigma.refresh({ skipIndexation: true });
    });

    // ── Drag: downNode ────────────────────────────────────────────────────
    // I2: we record WHICH node is being dragged; no layout computed here.
    sigma.on("downNode", ({ node, event }) => {
      dragState.draggedNode = node;
      dragState.hasMoved = false;
      dragState.downX = event.x;
      dragState.downY = event.y;
      // Highlight the node visually during drag
      sigmaGraph.setNodeAttribute(node, "highlighted", true);
      // Disable the mouse captor so the stage doesn't pan while we drag
      sigma.getMouseCaptor().enabled = false;
      sigma.refresh({ skipIndexation: true });
    });

    // ── Drag: moveBody ────────────────────────────────────────────────────
    // I2: ONLY the dragged node's x/y are updated — direct manipulation, not layout.
    // No other node is touched. No physics loop. No rAF.
    sigma.on("moveBody", ({ event }) => {
      if (dragState.draggedNode === null) return;

      // Prevent sigma's default stage-pan behaviour
      event.preventSigmaDefault();

      // Convert screen coords → graph-space coords (sigma v3 API)
      const pos = sigma.viewportToGraph({ x: event.x, y: event.y });

      // Ignore sub-threshold jitter so a tap doesn't register as a drag (mobile pin bug).
      const dx = event.x - dragState.downX;
      const dy = event.y - dragState.downY;
      if (!dragState.hasMoved && Math.hypot(dx, dy) < DRAG_THRESHOLD_PX) return;

      // Write ONLY the dragged node's position — I2: no other node touched
      sigmaGraph.setNodeAttribute(dragState.draggedNode, "x", pos.x);
      sigmaGraph.setNodeAttribute(dragState.draggedNode, "y", pos.y);

      dragState.hasMoved = true;
      // scheduleRefresh redraws without full re-indexation — fast path
      sigma.scheduleRefresh();
    });

    // ── Drag: upNode / upStage — end of drag ─────────────────────────────
    const endDrag = () => {
      if (dragState.draggedNode === null) return;

      const node = dragState.draggedNode;
      const moved = dragState.hasMoved;

      // Clear highlighted attribute
      sigmaGraph.setNodeAttribute(node, "highlighted", false);

      // Re-enable mouse captor (panning / zooming restored)
      sigma.getMouseCaptor().enabled = true;

      if (moved) {
        // Persist the new position to the backend (fire-and-forget).
        // I2: we are writing a USER-CHOSEN position back to the server, not
        // computing layout. The server stores it and returns it on the next
        // GET /graph so FA2 can re-incorporate it as a fixed seed.
        const finalX = sigmaGraph.getNodeAttribute(node, "x") as number;
        const finalY = sigmaGraph.getNodeAttribute(node, "y") as number;

        patchNodePosition(node, finalX, finalY).catch((err: unknown) => {
          // Non-critical — position is already updated locally in sigma.
          // Log quietly; do not surface to the user.
          if (err instanceof Error) {
            console.warn("[GraphViewer] patchNodePosition failed:", err.message);
          }
        });
      }

      dragState.draggedNode = null;
      dragState.hasMoved = false;
      sigma.refresh({ skipIndexation: true });
    };

    sigma.on("upNode", endDrag);
    sigma.on("upStage", endDrag);

    // ── Click (no drag) ───────────────────────────────────────────────────
    // clickNode fires after upNode; only show the tooltip when the pointer
    // did NOT move (i.e. it was a genuine click, not the end of a drag).
    sigma.on("clickNode", ({ node }) => {
      // sigma only fires clickNode when the pointer barely moved (a drag ends with upNode and does
      // NOT fire clickNode), so a genuine click reaches here. Obsidian-style: open the clicked
      // node's wiki page — select it and switch to the pages section, where NoteView renders it.
      // (v1.5.2 — previously this only showed an info tooltip and the page never opened.)
      hoverState.selectedNode = node;
      setSelectedNodeId(node);
      selectPage(node, "graph");
      setActiveSection("pages");
    });

    sigma.on("clickStage", () => {
      hoverState.selectedNode = null;
      setSelectedNodeId(null);
      setTooltip(null);
      setEdgeTooltip(null);
    });

    // ── R9-5: Edge click → show breakdown tooltip ─────────────────────────
    // sigma fires clickEdge when enableEdgeEvents:true (already set above).
    sigma.on("clickEdge", ({ edge, event }) => {
      const [src, tgt] = sigmaGraph.extremities(edge);
      setEdgeTooltip({ srcId: src, tgtId: tgt, position: { x: event.x, y: event.y } });
      // Close node tooltip if open
      setTooltip(null);
    });

    // ── Camera fit ────────────────────────────────────────────────────────
    // sigma/utils in v3.0.3 does not export fitViewportToNodes.
    // animatedReset() resets camera to the normalized graph center — equivalent fit.
    sigma.getCamera().animatedReset({
      duration: reducedMotion ? 0 : 500,
    });

    return () => {
      sigma.kill();
      sigmaRef.current = null;
    };
    // Rebuild sigma when graph data, color-mode, or theme colors change.
    // sigmaThemeColors change triggers re-instantiation with new render properties (I2).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges, colorMode, sigmaThemeColors]);

  // ── Sync selectedNodeId from store → announcement (aria-live) ────────────

  useEffect(() => {
    // Keep the reducer's ref in sync so the persistent selection ring follows the store.
    selectedNodeIdRef.current = selectedNodeId;
    if (!selectedNodeId) {
      setAnnouncement("");
      // Clear the ring: re-run reducers now that nothing is selected.
      sigmaRef.current?.refresh({ skipIndexation: true });
      return;
    }
    if (!sigmaRef.current) return;

    const graph = sigmaRef.current.getGraph();
    const attrs = graph.getNodeAttributes(selectedNodeId) as Attributes & {
      label?: string;
      nodeType?: string | null;
      degree?: number;
    };

    const title = attrs["label"] ?? selectedNodeId;
    const type = attrs["nodeType"] ?? "unknown type";
    const neighborCount = graph.neighbors(selectedNodeId).length;

    // F8: use i18n for screen-reader announcement (was hardcoded English)
    setAnnouncement(t("graph.nodeSelected", { title, type, count: neighborCount }));

    // Trigger a refresh so sigma re-applies reducers with updated selectedNode
    sigmaRef.current.refresh({ skipIndexation: true });
  }, [selectedNodeId, t]);

  const handleTooltipClose = useCallback(() => {
    setSelectedNodeId(null);
    setTooltip(null);
    setAnnouncement("");
  }, [setSelectedNodeId]);

  // ── Camera controls — zoom in / out / fit ─────────────────────────────────
  // These are simple camera calls; I2 is preserved (no layout algorithm invoked).
  // reducedMotion is read from the module-level const declared above the component.

  const handleZoomIn = useCallback(() => {
    sigmaRef.current?.getCamera().animatedZoom({ duration: reducedMotion ? 0 : 200 });
  }, []);

  const handleZoomOut = useCallback(() => {
    sigmaRef.current?.getCamera().animatedUnzoom({ duration: reducedMotion ? 0 : 200 });
  }, []);

  const handleFit = useCallback(() => {
    sigmaRef.current?.getCamera().animatedReset({ duration: reducedMotion ? 0 : 300 });
  }, []);

  // ── GR2: In-graph search — find node by title substring, select + camera center ──
  // Client-side only; nodes are already in the store (I3: computed on change, not per frame).
  const handleSearch = useCallback(
    (query: string) => {
      if (!query.trim() || !sigmaRef.current) return;
      const q = query.toLowerCase();
      // Find first matching node in the sigma graph
      const sigma = sigmaRef.current;
      const graph = sigma.getGraph();
      let matchKey: string | null = null;
      graph.forEachNode((key, attrs) => {
        if (matchKey !== null) return;
        const label = ((attrs["label"] as string | undefined) ?? "").toLowerCase();
        if (label.includes(q)) matchKey = key;
      });
      if (matchKey === null) return;
      // Select the node (triggers aria announcement + tree sync)
      selectPage(matchKey, "graph");
      setSelectedNodeId(matchKey);
      // Animate camera to center on the found node's precomputed coords (I2-safe: read-only)
      const attrs = graph.getNodeAttributes(matchKey);
      const x = attrs["x"] as number;
      const y = attrs["y"] as number;
      sigma.getCamera().animate({ x, y, ratio: 0.3 }, { duration: reducedMotion ? 0 : 400 });
    },
    [selectPage, setSelectedNodeId],
  );

  // ── GR4: Reset — clear ALL filters (type + GI-2) + fit camera ───────────────
  const handleReset = useCallback(() => {
    clearAllGraphFilters(); // clears filterNodeTypes + hideMetaTypes/hideIsolated/minLinks/maxLinks/nodeSizeScale/spacingScale
    handleFit();
  }, [clearAllGraphFilters, handleFit]);

  // ── GR7: Fullscreen — Fullscreen API on the graph root container ───────────
  const handleFullscreen = useCallback(() => {
    const el = graphRootRef.current;
    if (!el) return;
    if (!document.fullscreenElement) {
      el.requestFullscreen().catch((err: unknown) => {
        if (err instanceof Error) console.warn("[GraphViewer] fullscreen failed:", err.message);
      });
    } else {
      document.exitFullscreen().catch(() => {
        /* ignore */
      });
    }
  }, []);

  // ── Insights panel toggle — shared via graphStore (sibling GraphInsightsPanel reads it)
  const showInsightsPanel = useGraphStore(selectShowInsightsPanel);
  const setShowInsightsPanel = useGraphStore(selectSetShowInsightsPanel);

  const handleToggleInsights = useCallback(() => {
    setShowInsightsPanel(!showInsightsPanel);
  }, [showInsightsPanel, setShowInsightsPanel]);

  // ── insightCount — computed from current nodes/edges/communities (I3: memoized, not per-frame)
  // I3 / AC-F4-6: computeGraphInsights scans the whole graph (surprising connections +
  // knowledge gaps) — a >50ms main-thread task on large graphs. It MUST NOT run on the
  // load/render critical path (it would trip the long-task budget). Compute the toolbar
  // badge count lazily — only once the user opens the Insights panel — and even then at
  // idle time. Before first open the badge shows no number; the initial graph render
  // stays long-task-free.
  const [insightCount, setInsightCount] = useState(0);
  useEffect(() => {
    if (!showInsightsPanel || nodes.length === 0) {
      return;
    }
    let cancelled = false;
    const compute = () => {
      if (!cancelled) setInsightCount(computeGraphInsights(nodes, edges, communities).total);
    };
    const w = window as typeof window & {
      requestIdleCallback?: (cb: () => void, opts?: { timeout: number }) => number;
      cancelIdleCallback?: (h: number) => void;
    };
    let handle: number;
    if (typeof w.requestIdleCallback === "function") {
      handle = w.requestIdleCallback(compute, { timeout: 1000 });
    } else {
      handle = window.setTimeout(compute, 300);
    }
    return () => {
      cancelled = true;
      if (typeof w.cancelIdleCallback === "function") w.cancelIdleCallback(handle);
      else window.clearTimeout(handle);
    };
  }, [showInsightsPanel, nodes, edges, communities]);

  return (
    // I4: this container holds sigma's single <canvas> + a handful of overlay divs.
    // Total DOM nodes inside: <div#sigma-container> + <canvas> + aria-live + overlays = ~10 → well under 20.
    <div
      id="graph-root"
      ref={graphRootRef}
      role="application"
      aria-label="Knowledge graph"
      style={{
        position: "relative",
        width: "100%",
        height: "100%",
        overflow: "hidden",
        background: "var(--syn-bg)",
        display: "flex",
        flexDirection: "column",
      }}
    >
      {/* Keyframes for the spinning refresh icon in the toolbar */}
      <style>{`@keyframes syn-spin { to { transform: rotate(360deg); } }`}</style>

      {/* GR1–GR5, GR7, GI-2: Graph header with stats, search, filter, reset, fullscreen, color-mode, insights, refresh */}
      <GraphHeader
        nodes={nodes}
        edges={edges}
        totalNodes={totalNodes}
        filterNodeTypes={filterNodeTypes}
        toggleFilterNodeType={toggleFilterNodeType}
        clearFilterNodeTypes={clearFilterNodeTypes}
        onSearch={handleSearch}
        onReset={handleReset}
        onFullscreen={handleFullscreen}
        graphContainerRef={graphRootRef}
        colorMode={colorMode}
        onSetColorMode={setColorMode}
        showInsights={showInsightsPanel}
        onToggleInsights={handleToggleInsights}
        insightCount={insightCount}
        onRefresh={handleRegenerate}
        regenerating={regenerating}
        regenMsg={regenMsg}
        hideMetaTypes={hideMetaTypes}
        onSetHideMetaTypes={setHideMetaTypes}
        hideIsolated={hideIsolated}
        onSetHideIsolated={setHideIsolated}
        minLinks={minLinks}
        onSetMinLinks={setMinLinks}
        maxLinks={maxLinks}
        onSetMaxLinks={setMaxLinks}
        nodeSizeScale={nodeSizeScale}
        onSetNodeSizeScale={setNodeSizeScale}
        spacingScale={spacingScale}
        onSetSpacingScale={setSpacingScale}
        onClearAllFilters={clearAllGraphFilters}
      />
      {/* Canvas area wrapper: flex:1, position:relative so all absolute overlays
          (regenerate, zoom, legend, tooltips) are positioned relative to the canvas
          area — NOT the full graph-root that now includes the header above it. */}
      <div style={{ flex: 1, minHeight: 0, position: "relative", overflow: "hidden" }}>
        {/* sigma mounts ONE WebGL <canvas> here — I4.
          Background is set from the resolved --syn-bg token (ADR-0048 §T1).
          sigma inherits the container background for its WebGL clear color.
          Both inline style and CSS var are set so sigma and the DOM agree.
          Mobile/PWA (R10-5, AC-R10-5-3): touch-action: none is applied to
          #sigma-container via theme.css @media (max-width: 767px). This hands
          all pointer events exclusively to sigma's touch handler, enabling
          pinch-zoom without triggering the browser's native page-scroll/zoom.
          I2 is preserved: sigma's camera.animatedZoom/animatedUnzoom scale the
          precomputed coordinates — no FA2 re-invocation, no main-thread layout. */}
        <div
          id="sigma-container"
          ref={containerRef}
          style={{ width: "100%", height: "100%", background: sigmaThemeColors.bg }}
        />

        {/* Aria-live region — announces node selection for screen readers */}
        <div
          aria-live="polite"
          aria-atomic="true"
          style={{
            position: "absolute",
            width: 1,
            height: 1,
            overflow: "hidden",
            clip: "rect(0 0 0 0)",
            whiteSpace: "nowrap",
            border: 0,
            top: 0,
            left: 0,
          }}
        >
          {announcement}
        </div>

        {/* Zoom / fit control cluster — top-right of canvas area (reference layout) */}
        <div
          className="syn-card"
          style={{
            position: "absolute",
            top: 12,
            right: 12,
            padding: "4px",
            zIndex: 5,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 2,
            userSelect: "none",
          }}
          aria-label={t("graph.zoomControlsLabel")}
          data-testid="graph-zoom-controls"
        >
          <button
            type="button"
            onClick={handleZoomIn}
            data-testid="graph-zoom-in"
            aria-label={t("graph.zoomIn")}
            title={t("graph.zoomIn")}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              width: 28,
              height: 28,
              border: "1px solid var(--syn-border)",
              borderRadius: 3,
              background: "var(--syn-surface)",
              color: "var(--syn-text-muted)",
              cursor: "pointer",
              padding: 0,
            }}
          >
            <ZoomIn size={14} strokeWidth={1.8} aria-hidden="true" />
          </button>
          <button
            type="button"
            onClick={handleZoomOut}
            data-testid="graph-zoom-out"
            aria-label={t("graph.zoomOut")}
            title={t("graph.zoomOut")}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              width: 28,
              height: 28,
              border: "1px solid var(--syn-border)",
              borderRadius: 3,
              background: "var(--syn-surface)",
              color: "var(--syn-text-muted)",
              cursor: "pointer",
              padding: 0,
            }}
          >
            <ZoomOut size={14} strokeWidth={1.8} aria-hidden="true" />
          </button>
          <button
            type="button"
            onClick={handleFit}
            data-testid="graph-fit"
            aria-label={t("graph.fit")}
            title={t("graph.fit")}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              width: 28,
              height: 28,
              border: "1px solid var(--syn-border)",
              borderRadius: 3,
              background: "var(--syn-surface)",
              color: "var(--syn-text-muted)",
              cursor: "pointer",
              padding: 0,
            }}
          >
            <Maximize2 size={14} strokeWidth={1.8} aria-hidden="true" />
          </button>
        </div>

        {/* Status bar overlay — bottom-center so it doesn't conflict with zoom controls top-right */}
        <StatusBar />

        {/* Legend overlay — CVD-safe: name + color swatch; switches on colorMode.
          "community" mode shows ONE row per Louvain community, labeled with
          communityDisplayName(c) — unique "{domain} · {subtopic}" per cluster.
          G3 (v1.3.14 fix): onCommunityClick wired only in "community" mode so clicking
          a community legend row opens CommunityPanel (GET /graph/communities/{id}). */}
        <GraphLegend
          colorMode={colorMode}
          communities={communities}
          nodes={nodes}
          {...(colorMode === "community"
            ? {
                onCommunityClick: (id: number) =>
                  setCommunityPanel({ id, color: colorForCommunity(id) }),
              }
            : {})}
        />

        {/* Community centroid labels overlay (community mode).
          "Comunità" toggle = per-Louvain-community grouping: one label per cluster at its
          nodes' centroid. Label = communityDisplayName(c) truncated to OVERLAY_LABEL_MAX_CHARS.
          Uses CentroidOverlay (string-keyed Map) with communityCentroids (memoized, I3).
          INVARIANT I2: reads server-provided x/y only; never mutates node positions or runs layout.
          INVARIANT I3: communityCentroids memoized via useMemo; only viewport projection per frame. */}
        <CentroidOverlay
          centroids={communityCentroids}
          sigmaRef={sigmaRef}
          active={colorMode === "community"}
          testId="community-overlay"
        />

        {/* R9-5: Community drill-down panel */}
        {communityPanel !== null && (
          <CommunityPanel
            communityId={communityPanel.id}
            communityColor={communityPanel.color}
            onClose={() => setCommunityPanel(null)}
            onNavigate={(pageId) => {
              setSelectedNodeId(pageId);
              setCommunityPanel(null);
            }}
          />
        )}

        {/* Tooltip — conditional, at most 1 visible at a time */}
        {tooltip !== null && (
          <NodeTooltip
            nodeId={tooltip.nodeId}
            position={tooltip.position}
            neighborCount={tooltip.neighborCount}
            onClose={handleTooltipClose}
          />
        )}

        {/* R9-5: Edge weight breakdown tooltip */}
        {edgeTooltip !== null && (
          <EdgeBreakdownTooltip
            srcId={edgeTooltip.srcId}
            tgtId={edgeTooltip.tgtId}
            position={edgeTooltip.position}
            cache={edgeDetailCache.current}
            onCached={handleEdgeCached}
            onClose={() => setEdgeTooltip(null)}
          />
        )}

        {/* Close canvas area wrapper */}
      </div>
    </div>
  );
};

export default GraphViewer;
