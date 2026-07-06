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

import React, { useCallback, useEffect, useRef, useState } from "react";
import { ZoomIn, ZoomOut, Maximize2, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { COMMUNITY_PALETTE, LOW_COHESION_THRESHOLD, colorForCommunity } from "./graphPalette";
import type { ColorMode } from "./graphPalette";
import Sigma from "sigma";
import type { Attributes } from "graphology-types";
import type { Settings } from "sigma/settings";
import type { NodeDisplayData, PartialButFor } from "sigma/types";
import { buildGraphologyGraph } from "../api/graphTransform";
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
import type { GraphCommunity, PageDetail } from "../api/types";
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
  useGraphMeta,
  useGraphStatus,
  useGraphStore,
} from "../store/graphStore";

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
// LIGHT THEME: hex values exactly match --syn-type-* tokens in theme.css.
// sigma.js cannot resolve CSS custom properties at canvas draw time, so concrete
// hex strings are required here — this is the one documented exception to token-only usage.
// If theme.css tokens change, update these values in sync.
//   --syn-type-concept:    #8250df  (purple)
//   --syn-type-entity:     #2563eb  (blue)
//   --syn-type-source:     #e16f24  (orange)
//   --syn-type-synthesis:  #cf222e  (red)
//   --syn-type-comparison: #1a7f37  (green)
//   --syn-type-query:      #16a34a  (green)
//   --syn-type-overview:   #b8860b  (amber)
//   --syn-type-other:      #6e7781
//   DEFAULT (--syn-text-dim): #8b949e

const TYPE_COLORS: Record<string, string> = {
  concept: "#8250df", // matches --syn-type-concept
  entity: "#2563eb", // matches --syn-type-entity
  source: "#e16f24", // matches --syn-type-source
  synthesis: "#cf222e", // matches --syn-type-synthesis
  comparison: "#1a7f37", // matches --syn-type-comparison
  query: "#16a34a", // matches --syn-type-query
  overview: "#b8860b", // matches --syn-type-overview
};

const DEFAULT_NODE_COLOR = "#6e7781"; // matches --syn-type-other

function colorForType(type: string | null): string {
  if (type === null) return DEFAULT_NODE_COLOR;
  return TYPE_COLORS[type] ?? DEFAULT_NODE_COLOR;
}

// ─── Re-export community palette identifiers for test isolation ───────────────
// COMMUNITY_PALETTE, LOW_COHESION_THRESHOLD, colorForCommunity, ColorMode are
// all imported from ./graphPalette (pure module, no sigma dependency) so they
// can be unit-tested in jsdom without WebGL2. See graphPalette.ts.
export { COMMUNITY_PALETTE, LOW_COHESION_THRESHOLD, colorForCommunity };
export type { ColorMode };

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
 * Build a halo hover drawer that uses the provided theme colors.
 * Draws a highlight ring around the hovered node, then the halo'd label.
 */
function makeDrawHaloNodeHover(themeColors: SigmaThemeColors) {
  const drawLabel = makeDrawHaloNodeLabel(themeColors);
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

    drawLabel(context, data, settings);
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

// ─── Legend ───────────────────────────────────────────────────────────────────
// CVD-safe: shows color swatch AND type/community label (redundant encoding, WCAG 1.4.1).

interface GraphLegendProps {
  /** Which color-mode is currently active — determines legend content. */
  colorMode: ColorMode;
  /** Community summary list from GET /graph (server-computed, I2). */
  communities: GraphCommunity[];
  /** Called when a community legend entry is clicked (R9-5). */
  onCommunityClick?: (id: number) => void;
}

const GraphLegend: React.FC<GraphLegendProps> = ({ colorMode, communities, onCommunityClick }) => {
  const { t } = useTranslation();

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
      {colorMode === "type" ? (
        <>
          {/* TYPE mode legend — CVD-safe: name + swatch (WCAG 1.4.1) */}
          <div
            style={{
              fontSize: 10,
              color: "var(--syn-text-muted)",
              marginBottom: 6,
              letterSpacing: "0.08em",
              fontWeight: 600,
            }}
          >
            {t("graph.legendNodeTypes")}
          </div>
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
              <span style={{ fontSize: 11, color: "var(--syn-text)", textTransform: "capitalize" }}>
                {type}
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
          </div>
        </>
      ) : (
        <>
          {/* COMMUNITY mode legend (server-driven, I2) */}
          <div
            style={{
              fontSize: 10,
              color: "var(--syn-text-muted)",
              marginBottom: 6,
              letterSpacing: "0.08em",
              fontWeight: 600,
            }}
          >
            {t("graph.legendCommunities")}
          </div>
          {communities.length === 0 ? (
            <span style={{ fontSize: 11, color: "var(--syn-text-dim)" }}>
              {t("common.unknown")}
            </span>
          ) : (
            communities.map((c) => {
              const isLowCohesion = c.cohesion < LOW_COHESION_THRESHOLD;
              const color = colorForCommunity(c.id);
              return (
                <div
                  key={c.id}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    marginBottom: 4,
                    cursor: onCommunityClick ? "pointer" : "default",
                  }}
                  data-testid={`community-legend-item-${c.id}`}
                  onClick={() => onCommunityClick?.(c.id)}
                  role={onCommunityClick ? "button" : undefined}
                  tabIndex={onCommunityClick ? 0 : undefined}
                  onKeyDown={(e) => {
                    if (onCommunityClick && (e.key === "Enter" || e.key === " ")) {
                      e.preventDefault();
                      onCommunityClick(c.id);
                    }
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
                  <span style={{ fontSize: 11, color: "var(--syn-text)" }}>
                    {t("graph.legendCommunityLabel", { id: c.id })}
                    <span style={{ color: "var(--syn-text-muted)", marginLeft: 4 }}>
                      {t("graph.legendCommunitySize", { size: c.size })}
                    </span>
                    {/* Low-cohesion warning — llm_wiki pattern */}
                    {isLowCohesion && (
                      <span
                        title={t("graph.legendCommunityLowCohesion")}
                        style={{
                          marginLeft: 4,
                          color: "var(--syn-amber, #d97706)",
                          fontWeight: 600,
                          fontSize: 10,
                        }}
                        data-testid={`community-low-cohesion-${c.id}`}
                        aria-label={t("graph.legendCommunityLowCohesion")}
                      >
                        !
                      </span>
                    )}
                  </span>
                </div>
              );
            })
          )}
          {/* Unassigned (-1) swatch */}
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
            <span style={{ fontSize: 11, color: "var(--syn-text-dim)" }}>—</span>
          </div>
        </>
      )}
    </div>
  );
};

// ─── Status bar ───────────────────────────────────────────────────────────────

const StatusBar: React.FC = () => {
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
        Loading graph...
      </div>
    );
  }

  return (
    <div
      className="syn-card"
      style={{
        position: "absolute",
        top: 12,
        right: 12,
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

  // sigma container ref — sigma mounts ONE WebGL <canvas> inside this div (I4)
  const containerRef = useRef<HTMLDivElement>(null);
  // sigma instance ref — kept outside React state to avoid re-render on mount
  const sigmaRef = useRef<Sigma<Attributes, Attributes, Attributes> | null>(null);

  // Resolved sigma theme colors — updated on mount and on theme change (ADR-0048 §T1)
  // React state drives re-render so sigma can be re-instantiated with correct colors.
  const [sigmaThemeColors, setSigmaThemeColors] = React.useState<SigmaThemeColors>(() =>
    readSigmaThemeColors(),
  );

  // Color-mode toggle: "type" (default) or "community" (llm_wiki pattern)
  const [colorMode, setColorMode] = useState<ColorMode>("type");

  // Tooltip state (React state — triggers re-render to show/hide tooltip)
  const [tooltip, setTooltip] = useState<TooltipState | null>(null);
  // Aria-live announcement text
  const [announcement, setAnnouncement] = useState<string>("");

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

  const handleCommunityClick = useCallback((id: number) => {
    const color = colorForCommunity(id);
    setCommunityPanel((prev) => (prev?.id === id ? null : { id, color }));
  }, []);

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
      setGraph(data.nodes, data.edges, data.data_version, cacheStatus, data.communities ?? []);
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

  useEffect(() => {
    const ctrl = new AbortController();
    setLoading(true);

    fetchGraph(vaultId, ctrl.signal)
      .then(({ data, cacheStatus }) => {
        setGraph(data.nodes, data.edges, data.data_version, cacheStatus, data.communities ?? []);
      })
      .catch((err: unknown) => {
        if (err instanceof Error && err.name !== "AbortError") {
          setError(err.message);
        }
      });

    return () => ctrl.abort();
  }, [vaultId, setGraph, setLoading, setError]);

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

    rawGraph.forEachNode((nodeKey, attrs) => {
      const nodeType = attrs["type"] as string | null | undefined;
      // Community id — -1 when unassigned or from older servers (non-breaking, set in graphTransform)
      const nodeCommunity = (attrs["community"] as number | undefined) ?? -1;
      // Color is determined by active color-mode (I2: community id comes from server)
      const nodeColor =
        colorMode === "community"
          ? colorForCommunity(nodeCommunity)
          : colorForType(nodeType ?? null);
      sigmaGraph.addNode(nodeKey, {
        x: attrs["x"] as number,
        y: attrs["y"] as number,
        label: attrs["label"] as string,
        size: attrs["size"] as number,
        color: nodeColor,
        // Store degree for reducers
        degree: attrs["degree"] as number,
        // Store type for reducers
        nodeType: nodeType ?? null,
        // Store community for reducers / tooltip
        nodeCommunity,
        // GL2: hub flag — nodeReducer uses this to force permanent label on top-K hubs
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
      // GL2 (B3-LOOK): threshold lowered from 13 → 8 so more labels appear on zoom-in,
      // matching the label density of nashsu/llm_wiki's graph view.
      labelDensity: 0.7,
      labelGridCellSize: 70,
      labelRenderedSizeThreshold: 8,

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

      // ── nodeReducer: Obsidian hover-dim + GL2 hub labels ────────────────
      nodeReducer(node: string, data: Attributes): Partial<NodeDisplayData> {
        const res: Partial<NodeDisplayData> & Attributes = { ...data };

        // GL2: hub nodes always show their label at rest (top-K by degree).
        // This is applied before hover-dim so hover can override it further.
        if ((data["isHub"] as boolean | undefined) === true) {
          res["forceLabel"] = true;
        }

        if (hoverState.hoveredNeighbors !== null) {
          const isHovered = node === hoverState.hoveredNode;
          const isNeighbor = hoverState.hoveredNeighbors.has(node);

          if (isHovered) {
            // Hovered node: highlighted, forced label, top z, slight size bump
            res["highlighted"] = true;
            res["forceLabel"] = true;
            res["zIndex"] = 2;
            res["size"] = ((data["size"] as number | undefined) ?? 8) * 1.15;
          } else if (isNeighbor) {
            // Neighbor: show label, raised z, deepened color so cluster pops on light bg
            res["forceLabel"] = true;
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
        }

        return res as Partial<NodeDisplayData>;
      },

      // ── edgeReducer: GL1 resting cull + Obsidian hover reveal ───────────
      // At rest: edges with hidden:true (weak edges per GL1) are not rendered.
      // On hover: incident edges are ALWAYS revealed regardless of GL1 threshold
      //   so the user can explore the full neighborhood even on large graphs.
      // Non-incident edges during hover: hidden (Obsidian dim).
      edgeReducer(edge: string, data: Attributes) {
        const res: Attributes = { ...data };

        if (hoverState.hoveredNode !== null) {
          const [src, tgt] = sigmaGraph.extremities(edge);
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
            res["color"] = dark ? "rgba(196,205,220,0.9)" : "rgba(89,99,110,0.85)";
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
    sigma.on("clickNode", ({ node, event }) => {
      // dragState.hasMoved is already reset by endDrag at this point,
      // so we use a separate check: if highlighted is still true the drag
      // just finished — actually upNode runs first, so hasMoved is false.
      // Simplest heuristic: sigma fires clickNode only when draggedEvents < threshold,
      // so we can safely always open the tooltip here (endDrag cleared hasMoved).
      // To be safe, skip tooltip if the node was dragged (position changed visibly).
      // We detect this by checking whether the node's stored position differs from
      // the sigmaGraph position — but that's expensive. Instead we track a separate
      // ref via the existing dragState: endDrag resets hasMoved=false AND sets
      // draggedNode=null, so by the time clickNode fires, dragState is clean.
      // sigma only fires clickNode when the pointer barely moved (draggedEvents < tolerance),
      // so this is safe to leave unconditional.
      const neighborCount = sigmaGraph.neighbors(node).length;
      hoverState.selectedNode = node;
      setSelectedNodeId(node);
      setTooltip({
        nodeId: node,
        position: { x: event.x, y: event.y },
        neighborCount,
      });
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
    if (!selectedNodeId) {
      setAnnouncement("");
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

  return (
    // I4: this container holds sigma's single <canvas> + a handful of overlay divs.
    // Total DOM nodes inside: <div#sigma-container> + <canvas> + aria-live + overlays = ~10 → well under 20.
    <div
      id="graph-root"
      role="application"
      aria-label="Knowledge graph"
      style={{
        position: "relative",
        width: "100%",
        height: "100%",
        overflow: "hidden",
        background: "var(--syn-bg)",
      }}
    >
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

      {/* Regenerate-graph control — top-left (top-right is the Insights panel).
          Reconnects cross-ingest links + recomputes FA2. */}
      <div
        className="syn-card"
        style={{
          position: "absolute",
          top: 12,
          left: 12,
          padding: "4px 6px",
          zIndex: 6,
          display: "flex",
          alignItems: "center",
          gap: 8,
          userSelect: "none",
        }}
        data-testid="graph-regenerate-toolbar"
      >
        <style>{`@keyframes syn-spin { to { transform: rotate(360deg); } }`}</style>
        {regenMsg !== null && (
          <span
            style={{ fontSize: 11, color: "var(--syn-text-muted)" }}
            data-testid="graph-regenerate-msg"
            role="status"
          >
            {regenMsg}
          </span>
        )}
        <button
          type="button"
          onClick={handleRegenerate}
          disabled={regenerating}
          data-testid="graph-regenerate"
          aria-label={t("graph.regenerate")}
          title={t("graph.regenerateTitle")}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            fontSize: 11,
            padding: "4px 10px",
            border: "1px solid var(--syn-border)",
            borderRadius: 3,
            background: "var(--syn-surface)",
            color: "var(--syn-text)",
            cursor: regenerating ? "default" : "pointer",
            opacity: regenerating ? 0.6 : 1,
          }}
        >
          <RefreshCw
            size={13}
            strokeWidth={1.8}
            aria-hidden="true"
            style={regenerating ? { animation: "syn-spin 0.9s linear infinite" } : undefined}
          />
          {regenerating ? t("graph.regenerating") : t("graph.regenerate")}
        </button>
      </div>

      {/* Zoom / fit control cluster — bottom-right, above color-mode toolbar */}
      <div
        className="syn-card"
        style={{
          position: "absolute",
          bottom: 60,
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

      {/* Color-mode toolbar — Type / Community toggle (llm_wiki pattern) */}
      <div
        className="syn-card"
        style={{
          position: "absolute",
          bottom: 16,
          right: 12,
          padding: "4px 6px",
          zIndex: 5,
          display: "flex",
          alignItems: "center",
          gap: 4,
          userSelect: "none",
        }}
        aria-label={t("graph.colorModeToggleLabel")}
        data-testid="color-mode-toolbar"
      >
        <span
          style={{
            fontSize: 10,
            color: "var(--syn-text-muted)",
            marginRight: 2,
            letterSpacing: "0.05em",
          }}
        >
          {t("graph.colorModeToggleLabel")}
        </span>
        <button
          type="button"
          onClick={() => setColorMode("type")}
          data-testid="color-mode-type"
          style={{
            fontSize: 11,
            padding: "2px 8px",
            borderRadius: 3,
            border: "1px solid var(--syn-border)",
            cursor: "pointer",
            background: colorMode === "type" ? "var(--syn-accent)" : "var(--syn-surface)",
            color: colorMode === "type" ? "#fff" : "var(--syn-text-muted)",
            fontWeight: colorMode === "type" ? 600 : 400,
          }}
          aria-pressed={colorMode === "type"}
        >
          {t("graph.colorModeType")}
        </button>
        <button
          type="button"
          onClick={() => setColorMode("community")}
          data-testid="color-mode-community"
          style={{
            fontSize: 11,
            padding: "2px 8px",
            borderRadius: 3,
            border: "1px solid var(--syn-border)",
            cursor: "pointer",
            background: colorMode === "community" ? "var(--syn-accent)" : "var(--syn-surface)",
            color: colorMode === "community" ? "#fff" : "var(--syn-text-muted)",
            fontWeight: colorMode === "community" ? 600 : 400,
          }}
          aria-pressed={colorMode === "community"}
        >
          {t("graph.colorModeCommunity")}
        </button>
      </div>

      {/* Status bar overlay */}
      <StatusBar />

      {/* Legend overlay — CVD-safe: name + color swatch; switches on colorMode */}
      <GraphLegend
        colorMode={colorMode}
        communities={communities}
        {...(colorMode === "community" ? { onCommunityClick: handleCommunityClick } : {})}
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
    </div>
  );
};

export default GraphViewer;
