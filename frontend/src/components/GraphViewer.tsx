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
import { ZoomIn, ZoomOut, Maximize2, RefreshCw, Maximize } from "lucide-react";
import { useTranslation } from "react-i18next";
import { COMMUNITY_PALETTE, LOW_COHESION_THRESHOLD, colorForCommunity, colorForDomain } from "./graphPalette";
import type { ColorMode } from "./graphPalette";
import { computeCommunityCentroids, computeDomainCentroids } from "./graphCommunityUtils";
import type { CommunityCentroid } from "./graphCommunityUtils";
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

// ─── Re-export community/domain palette + centroid utilities for test isolation ─
// These are all imported from pure modules (no sigma dependency) so they can be
// unit-tested in jsdom without WebGL2. See graphPalette.ts, graphCommunityUtils.ts.
export { COMMUNITY_PALETTE, LOW_COHESION_THRESHOLD, colorForCommunity, colorForDomain };
export type { ColorMode };
// computeCommunityCentroids / computeDomainCentroids re-exported from graphCommunityUtils
// for tests that import from GraphViewer directly (backward compat).
export { computeCommunityCentroids, computeDomainCentroids };

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
  "other",
] as const;

// ─── GraphHeader (GR1–GR5, GR7) ─────────────────────────────────────────────
// Stats chips + in-graph search + filter popover + reset + fullscreen.
// Sits above the sigma canvas in GraphPanel; all operations are client-side (I2).

interface GraphHeaderProps {
  /** In-graph nodes from the store payload (GET /graph nodes array, ~816 of 986) */
  nodes: GraphNode[];
  /** In-graph edges from the store payload (full graph edge set including source-overlap) */
  edges: GraphEdge[];
  /**
   * GR1: All live vault pages (pre-graph-inclusion, from GET /graph total_nodes field).
   * null = old backend that doesn't expose this field yet.
   * denominator for the pages chip: total_nodes = 986 (in-graph 816 + isolated/dropped 170).
   * hiddenCount = total_nodes - visibleNodes (covers filtered-out + not-in-graph pages).
   */
  totalNodes: number | null;
  filterNodeTypes: Set<string>;
  toggleFilterNodeType: (type: string) => void;
  clearFilterNodeTypes: () => void;
  onSearch: (query: string) => void;
  onReset: () => void;
  onFullscreen: () => void;
  graphContainerRef: React.RefObject<HTMLDivElement | null>;
}

const GraphHeader: React.FC<GraphHeaderProps> = ({
  nodes,
  edges,
  totalNodes,
  filterNodeTypes,
  toggleFilterNodeType,
  clearFilterNodeTypes,
  onSearch,
  onReset,
  onFullscreen,
  graphContainerRef: _graphContainerRef,
}) => {
  const { t } = useTranslation();
  const [searchQuery, setSearchQuery] = useState("");
  const [filterOpen, setFilterOpen] = useState(false);
  const filterRef = useRef<HTMLDivElement>(null);

  // GR1 — PAGES chip:
  //   denominator = total_nodes (all live vault pages, e.g. 986)
  //   numerator   = visible in-graph nodes after client filter
  //   hiddenCount = total_nodes - visibleNodes
  //     → covers both: (a) in-graph nodes filtered out, (b) pages not in the graph at all
  //   Falls back to nodes.length when backend doesn't expose total_nodes yet.
  const visibleNodes = filterNodeTypes.size === 0
    ? nodes.length
    : nodes.filter((n) => filterNodeTypes.has(n.type ?? "other")).length;
  const displayTotalNodes = totalNodes ?? nodes.length;
  const hiddenCount = displayTotalNodes - visibleNodes;

  // GR1 — LINKS chip:
  //   denominator = edges.length (full graph edge set incl. source-overlap, e.g. 4213)
  //   numerator   = edges whose both endpoints are in the active filter (visible edges)
  //   This makes GL1-culled edges visible to the user as "not shown" — mirrors llm_wiki.
  //   We compute this from the store's edges array (type info lives on nodes).
  //   Build a fast lookup: nodeId → type key
  const nodeTypeMap = React.useMemo(() => {
    const m = new Map<string, string>();
    for (const n of nodes) m.set(n.id, n.type ?? "other");
    return m;
  }, [nodes]);

  const visibleEdges = React.useMemo(() => {
    if (filterNodeTypes.size === 0) return edges.length;
    return edges.filter((e) => {
      const srcType = nodeTypeMap.get(e.source) ?? "other";
      const tgtType = nodeTypeMap.get(e.target) ?? "other";
      return filterNodeTypes.has(srcType) && filterNodeTypes.has(tgtType);
    }).length;
  }, [edges, filterNodeTypes, nodeTypeMap]);

  const totalEdgesCount = edges.length;

  // GR2: search handler — called on input change; finds the first matching node
  const handleSearchChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const q = e.target.value;
    setSearchQuery(q);
    onSearch(q);
  }, [onSearch]);

  const handleSearchClear = useCallback(() => {
    setSearchQuery("");
    onSearch("");
  }, [onSearch]);

  const handleSearchKeyDown = useCallback((e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Escape") {
      setSearchQuery("");
      onSearch("");
      (e.currentTarget as HTMLInputElement).blur();
    }
  }, [onSearch]);

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

  const hasActiveFilter = filterNodeTypes.size > 0;

  return (
    <div
      data-testid="graph-header"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "6px 10px",
        borderBottom: "1px solid var(--syn-border)",
        background: "var(--syn-surface)",
        flexShrink: 0,
        flexWrap: "wrap",
        minHeight: 38,
        // Must be above the insights panel (z-index:10) and any canvas overlays
        position: "relative",
        zIndex: 15,
      }}
    >
      {/* GR1: Stats chips */}
      <div style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
        {/* Pages: visibleNodes/totalNodes — hidden covers both filtered + not-in-graph */}
        <span
          data-testid="graph-header-nodes"
          style={{ fontSize: 11, color: "var(--syn-text-muted)", whiteSpace: "nowrap" }}
        >
          {visibleNodes}/{displayTotalNodes} {t("graph.header.pages")}
        </span>
        {/* Links: visibleEdges/totalEdgesInGraph — GL1-culled show as "not shown" */}
        {totalEdgesCount > 0 && (
          <>
            <span style={{ fontSize: 11, color: "var(--syn-border)" }}>·</span>
            <span
              data-testid="graph-header-edges"
              style={{ fontSize: 11, color: "var(--syn-text-muted)", whiteSpace: "nowrap" }}
            >
              {visibleEdges}/{totalEdgesCount} {t("graph.header.links")}
            </span>
          </>
        )}
        {/* Orange "N hidden" chip — only when filter excludes some nodes */}
        {hiddenCount > 0 && (
          <>
            <span style={{ fontSize: 11, color: "var(--syn-border)" }}>·</span>
            <span
              data-testid="graph-header-hidden"
              style={{
                fontSize: 11,
                color: "#d97706",
                background: "color-mix(in srgb, #d97706 12%, transparent)",
                border: "1px solid color-mix(in srgb, #d97706 30%, transparent)",
                borderRadius: 3,
                padding: "0px 5px",
                whiteSpace: "nowrap",
              }}
            >
              {hiddenCount} {t("graph.header.hidden")}
            </span>
          </>
        )}
      </div>

      {/* Spacer */}
      <div style={{ flex: 1 }} />

      {/* GR2: In-graph search */}
      <div style={{ position: "relative", flexShrink: 0 }}>
        <input
          data-testid="graph-search-input"
          type="text"
          value={searchQuery}
          onChange={handleSearchChange}
          onKeyDown={handleSearchKeyDown}
          placeholder={t("graph.header.searchPlaceholder")}
          aria-label={t("graph.header.searchPlaceholder")}
          style={{
            fontSize: 11,
            padding: "3px 22px 3px 7px",
            border: "1px solid var(--syn-border)",
            borderRadius: 4,
            background: "var(--syn-bg)",
            color: "var(--syn-text)",
            width: 160,
            outline: "none",
          }}
        />
        {searchQuery.length > 0 && (
          <button
            type="button"
            onClick={handleSearchClear}
            aria-label={t("common.close")}
            style={{
              position: "absolute",
              right: 4,
              top: "50%",
              transform: "translateY(-50%)",
              background: "none",
              border: "none",
              color: "var(--syn-text-dim)",
              cursor: "pointer",
              fontSize: 12,
              lineHeight: 1,
              padding: 0,
            }}
          >
            ×
          </button>
        )}
      </div>

      {/* GR3: Filter popover button */}
      <div ref={filterRef} style={{ position: "relative", flexShrink: 0 }}>
        <button
          type="button"
          data-testid="graph-filter-button"
          onClick={() => setFilterOpen((o) => !o)}
          aria-label={t("graph.header.filter")}
          aria-expanded={filterOpen}
          style={{
            fontSize: 11,
            padding: "3px 8px",
            border: `1px solid ${hasActiveFilter ? "var(--syn-accent)" : "var(--syn-border)"}`,
            borderRadius: 4,
            background: hasActiveFilter ? "color-mix(in srgb, var(--syn-accent) 10%, var(--syn-surface))" : "var(--syn-surface)",
            color: hasActiveFilter ? "var(--syn-accent)" : "var(--syn-text-muted)",
            cursor: "pointer",
            whiteSpace: "nowrap",
            fontWeight: hasActiveFilter ? 600 : 400,
          }}
        >
          {t("graph.header.filter")}
          {hasActiveFilter && ` (${filterNodeTypes.size})`}
        </button>

        {filterOpen && (
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
              minWidth: 160,
              boxShadow: "0 4px 16px rgba(0,0,0,0.12)",
            }}
          >
            <div
              style={{
                fontSize: 10,
                color: "var(--syn-text-muted)",
                letterSpacing: "0.06em",
                fontWeight: 600,
                marginBottom: 6,
              }}
            >
              {t("graph.header.filterNodeTypes")}
            </div>
            {ALL_NODE_TYPES.map((type) => {
              const checked = filterNodeTypes.size === 0 || filterNodeTypes.has(type);
              // Count nodes of this type in the current store nodes
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
            {hasActiveFilter && (
              <button
                type="button"
                onClick={() => {
                  clearFilterNodeTypes();
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

      {/* GR4: Reset */}
      <button
        type="button"
        data-testid="graph-header-reset"
        onClick={onReset}
        title={t("graph.header.reset")}
        aria-label={t("graph.header.reset")}
        style={{
          fontSize: 11,
          padding: "3px 8px",
          border: "1px solid var(--syn-border)",
          borderRadius: 4,
          background: "var(--syn-surface)",
          color: "var(--syn-text-muted)",
          cursor: "pointer",
          whiteSpace: "nowrap",
          flexShrink: 0,
        }}
      >
        {t("graph.header.reset")}
      </button>

      {/* GR7: Fullscreen */}
      <button
        type="button"
        data-testid="graph-header-fullscreen"
        onClick={onFullscreen}
        title={t("graph.header.fullscreen")}
        aria-label={t("graph.header.fullscreen")}
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          width: 26,
          height: 26,
          border: "1px solid var(--syn-border)",
          borderRadius: 4,
          background: "var(--syn-surface)",
          color: "var(--syn-text-muted)",
          cursor: "pointer",
          flexShrink: 0,
          padding: 0,
        }}
      >
        <Maximize size={13} strokeWidth={1.8} aria-hidden="true" />
      </button>
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
  /** Called when a community legend entry is clicked (R9-5). Unused in community/domain mode. */
  onCommunityClick?: (id: number) => void;
  /**
   * All graph nodes — used in "community" mode (domain grouping) to aggregate node
   * counts per domain. ONE row per distinct domain name — no Louvain duplicates.
   * I3: the caller (GraphViewer) passes this from the store; no extra subscription here.
   */
  nodes?: GraphNode[];
}

const GraphLegend: React.FC<GraphLegendProps> = ({ colorMode, communities: _communities, onCommunityClick: _onCommunityClick, nodes = [] }) => {
  const { t } = useTranslation();

  // COMMUNITY mode = domain grouping: aggregate nodes by domain — one row per distinct name.
  // Sorted by count desc; untagged (null/empty domain) goes into a separate bucket at end.
  // I3: computed via useMemo so it only runs when nodes/colorMode change, not per render.
  const domainRows = React.useMemo<{ named: Array<{ domain: string; count: number }>; untaggedCount: number }>(() => {
    if (colorMode !== "community") return { named: [], untaggedCount: 0 };
    const counts = new Map<string | null, number>();
    for (const n of nodes) {
      const d = n.domain ?? null;
      counts.set(d, (counts.get(d) ?? 0) + 1);
    }
    const named: Array<{ domain: string; count: number }> = [];
    let untaggedCount = 0;
    for (const [d, c] of counts) {
      if (d === null || d.trim() === "") {
        untaggedCount += c;
      } else {
        named.push({ domain: d, count: c });
      }
    }
    named.sort((a, b) => b.count - a.count);
    return { named, untaggedCount };
  }, [colorMode, nodes]);

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
      aria-label={colorMode === "type" ? "Graph node type legend" : "Graph domain legend"}
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
          {/* COMMUNITY mode = DOMAIN grouping legend.
              ONE row per distinct domain name (SAM, Procurement, …) — no duplicate Louvain entries.
              Aggregated from GraphNode.domain (server-provided); sorted by node count desc.
              Untagged nodes (domain=null) are grouped into "Senza dominio" at the bottom.
              I3: domainRows computed via useMemo above; no work per render frame.
              I2: domain values are from server — client never assigns or computes them. */}
          <div
            style={{
              fontSize: 10,
              color: "var(--syn-text-muted)",
              marginBottom: 6,
              letterSpacing: "0.08em",
              fontWeight: 600,
            }}
          >
            {t("graph.legendDomains")}
          </div>
          {domainRows.named.length === 0 && domainRows.untaggedCount === 0 ? (
            <span style={{ fontSize: 11, color: "var(--syn-text-dim)" }}>
              {t("common.unknown")}
            </span>
          ) : (
            <>
              {domainRows.named.map(({ domain, count }) => (
                <div
                  key={domain}
                  data-testid={`domain-legend-item-${domain}`}
                  style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}
                >
                  <span
                    style={{
                      width: 9,
                      height: 9,
                      borderRadius: "50%",
                      background: colorForDomain(domain),
                      flexShrink: 0,
                      boxShadow: `0 0 0 1px rgba(0,0,0,0.12)`,
                    }}
                    aria-hidden="true"
                  />
                  <span style={{ fontSize: 11, color: "var(--syn-text)" }}>
                    <span
                      data-testid={`domain-legend-name-${domain}`}
                      style={{ fontWeight: 500 }}
                    >
                      {domain}
                    </span>
                    <span style={{ color: "var(--syn-text-muted)", marginLeft: 4 }}>
                      {t("graph.legendCommunitySize", { size: count })}
                    </span>
                  </span>
                </div>
              ))}
              {/* "Senza dominio" / Untagged bucket — last row */}
              {domainRows.untaggedCount > 0 && (
                <div
                  data-testid="domain-legend-untagged"
                  style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 2 }}
                >
                  <span
                    style={{
                      width: 9,
                      height: 9,
                      borderRadius: "50%",
                      background: "#8b949e",
                      flexShrink: 0,
                      boxShadow: `0 0 0 1px rgba(0,0,0,0.12)`,
                    }}
                    aria-hidden="true"
                  />
                  <span style={{ fontSize: 11, color: "var(--syn-text-dim)" }}>
                    {t("graph.legendDomainUntagged")}
                    <span style={{ color: "var(--syn-text-muted)", marginLeft: 4 }}>
                      {t("graph.legendCommunitySize", { size: domainRows.untaggedCount })}
                    </span>
                  </span>
                </div>
              )}
            </>
          )}
        </>
      )}
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

function truncateOverlayLabel(label: string): string {
  if (label.length <= OVERLAY_LABEL_MAX_CHARS) return label;
  return label.slice(0, OVERLAY_LABEL_MAX_CHARS - 1) + "…";
}

const CentroidOverlay: React.FC<CentroidOverlayProps> = ({ centroids, sigmaRef, active, testId = "community-overlay" }) => {
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

      // One pass: update each label element's transform. Elements are keyed by data-cid.
      for (const [cid, centroid] of centroids) {
        const el = overlay.querySelector<HTMLElement>(`[data-cid="${String(cid)}"]`);
        if (!el) continue;
        const vp = s.graphToViewport({ x: centroid.x, y: centroid.y });
        // Translate so the label is centered on the centroid
        el.style.transform = `translate(calc(${vp.x}px - 50%), calc(${vp.y}px - 50%))`;
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
  // GR3: node-type filter from store (I2-safe: visibility only, never re-layout)
  // Use a ref so the sigma reducers always read the latest filter without rebuilding sigma.
  const filterNodeTypes = useGraphStore(selectFilterNodeTypes);
  const filterNodeTypesRef = useRef<Set<string>>(filterNodeTypes);
  const toggleFilterNodeType = useGraphStore(selectToggleFilterNodeType);
  const clearFilterNodeTypes = useGraphStore(selectClearFilterNodeTypes);
  // GR1: total vault pages from backend (null = old server)
  const totalNodes = useGraphStore(selectTotalNodes);
  // GR2: selectPage action for search-triggered navigation
  const selectPage = useGraphStore(selectSelectPage);

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

  // Color-mode toggle: "community" (default — groups by DOMAIN, one cluster per domain name)
  // or "type" (groups by page type: concept, entity, source, …).
  // Note: despite the name "community", this mode uses GraphNode.domain for coloring,
  // not Louvain community ids. This gives one unified SAM cluster, one Procurement cluster,
  // with no duplicates — exactly what the user expects from "unisci per dominio".
  const [colorMode, setColorMode] = useState<ColorMode>("community");

  // Tooltip state (React state — triggers re-render to show/hide tooltip)
  const [tooltip, setTooltip] = useState<TooltipState | null>(null);
  // Aria-live announcement text
  const [announcement, setAnnouncement] = useState<string>("");

  // ── Domain centroid map (I3 — memoized; recomputed only when nodes change)
  // Active in "community" color mode (= domain grouping).
  // INVARIANT I2: only reads server-provided x/y and domain field — never mutates coords.
  // INVARIANT I3: computed once per nodes change, NOT per sigma frame.
  const domainCentroids = useMemo(
    () => computeDomainCentroids(nodes),
    [nodes],
  );

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
      setGraph(data.nodes, data.edges, data.data_version, cacheStatus, data.communities ?? [], data.total_nodes ?? null, data.total_edges ?? null);
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
        setGraph(data.nodes, data.edges, data.data_version, cacheStatus, data.communities ?? [], data.total_nodes ?? null, data.total_edges ?? null);
      })
      .catch((err: unknown) => {
        if (err instanceof Error && err.name !== "AbortError") {
          setError(err.message);
        }
      });

    return () => ctrl.abort();
  }, [vaultId, setGraph, setLoading, setError]);

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
      // "community" mode groups by DOMAIN (one color per domain name), NOT by Louvain id —
      // this ensures one unified cluster per domain (SAM, Procurement, …) with no duplicates.
      const nodeColor =
        colorMode === "community"
          ? colorForDomain(nodeDomain)
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

      // ── edgeReducer: GR3 filter + GL1 resting cull + Obsidian hover reveal ─
      // At rest: edges with hidden:true (weak edges per GL1) are not rendered.
      // GR3: edges whose source or target is filtered out are also hidden.
      // On hover: incident edges are ALWAYS revealed regardless of GL1 threshold
      //   so the user can explore the full neighborhood even on large graphs.
      // Non-incident edges during hover: hidden (Obsidian dim).
      edgeReducer(edge: string, data: Attributes) {
        const res: Attributes = { ...data };

        // GR3: hide edge if either endpoint type is filtered out.
        // I2-safe: visibility only, no coord mutation.
        const activeFilter = filterNodeTypesRef.current;
        if (activeFilter.size > 0) {
          const [src, tgt] = sigmaGraph.extremities(edge);
          const srcType = (sigmaGraph.getNodeAttribute(src, "nodeType") as string | null | undefined) ?? null;
          const tgtType = (sigmaGraph.getNodeAttribute(tgt, "nodeType") as string | null | undefined) ?? null;
          const srcKey = srcType ?? "other";
          const tgtKey = tgtType ?? "other";
          if (!activeFilter.has(srcKey) || !activeFilter.has(tgtKey)) {
            res["hidden"] = true;
            return res;
          }
        }

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

  // ── GR2: In-graph search — find node by title substring, select + camera center ──
  // Client-side only; nodes are already in the store (I3: computed on change, not per frame).
  const handleSearch = useCallback((query: string) => {
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
    sigma.getCamera().animate(
      { x, y, ratio: 0.3 },
      { duration: reducedMotion ? 0 : 400 },
    );
  }, [selectPage, setSelectedNodeId]);

  // ── GR4: Reset — clear filters + fit camera ────────────────────────────────
  const handleReset = useCallback(() => {
    clearFilterNodeTypes();
    handleFit();
  }, [clearFilterNodeTypes, handleFit]);

  // ── GR7: Fullscreen — Fullscreen API on the graph root container ───────────
  const handleFullscreen = useCallback(() => {
    const el = graphRootRef.current;
    if (!el) return;
    if (!document.fullscreenElement) {
      el.requestFullscreen().catch((err: unknown) => {
        if (err instanceof Error) console.warn("[GraphViewer] fullscreen failed:", err.message);
      });
    } else {
      document.exitFullscreen().catch(() => {/* ignore */});
    }
  }, []);

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
      {/* GR1–GR5, GR7: Graph header with stats, search, filter, reset, fullscreen */}
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

      {/* Color-mode toolbar — Tipo · Dominio · Comunità (domain is default) */}
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
        {/* Tipo — color by page type */}
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
        {/* Comunità — groups by DOMAIN (one color + one legend row per domain name).
            Colors use colorForDomain(node.domain) — stable djb2 hash → DOMAIN_PALETTE.
            One unified "SAM" cluster, one "Procurement" cluster, … — no Louvain duplicates. */}
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

      {/* Legend overlay — CVD-safe: name + color swatch; switches on colorMode.
          "community" mode shows ONE row per domain name (no Louvain duplicates). */}
      <GraphLegend
        colorMode={colorMode}
        communities={communities}
        nodes={nodes}
      />

      {/* Domain centroid labels overlay (community/domain mode).
          "Comunità" toggle = domain grouping: one label per domain at its nodes' centroid.
          Uses CentroidOverlay (string-keyed Map) with domainCentroids (memoized, I3).
          INVARIANT I2: reads server-provided x/y only; never mutates node positions or runs layout.
          INVARIANT I3: domainCentroids memoized via useMemo; only viewport projection per frame. */}
      <CentroidOverlay
        centroids={domainCentroids}
        sigmaRef={sigmaRef}
        active={colorMode === "community"}
        testId="domain-overlay"
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
