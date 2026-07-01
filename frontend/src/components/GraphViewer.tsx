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
import { useTranslation } from "react-i18next";
import {
  COMMUNITY_PALETTE,
  LOW_COHESION_THRESHOLD,
  colorForCommunity,
} from "./graphPalette";
import type { ColorMode } from "./graphPalette";
import Sigma from "sigma";
import type { Attributes } from "graphology-types";
import type { Settings } from "sigma/settings";
import type { NodeDisplayData, PartialButFor } from "sigma/types";
import { buildGraphologyGraph } from "../api/graphTransform";
import { fetchGraph, fetchPageDetail, patchNodePosition } from "../api/graphClient";
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
  typeof window !== "undefined" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

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
  concept:    "#8250df", // matches --syn-type-concept
  entity:     "#2563eb", // matches --syn-type-entity
  source:     "#e16f24", // matches --syn-type-source
  synthesis:  "#cf222e", // matches --syn-type-synthesis
  comparison: "#1a7f37", // matches --syn-type-comparison
  query:      "#16a34a", // matches --syn-type-query
  overview:   "#b8860b", // matches --syn-type-overview
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
// Light halo (#ffffff, lineWidth 3) then dark fill (#1f2328) — ~18:1 contrast on white.

type LabelDrawData = PartialButFor<NodeDisplayData, "x" | "y" | "size" | "label" | "color">;

function drawHaloNodeLabel(
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

  // Halo stroke (white) — improves readability on the light graph background
  context.strokeStyle = "#ffffff"; // --syn-bg
  context.lineWidth = 3;
  context.lineJoin = "round";
  context.strokeText(data.label, x, y);

  // Label fill (dark near-black — --syn-text)
  context.fillStyle = "#1f2328"; // --syn-text
  context.fillText(data.label, x, y);
}

// ─── Halo hover drawer ────────────────────────────────────────────────────────
// Draws a highlight ring around the hovered node, then the halo'd label.

function drawHaloNodeHover(
  context: CanvasRenderingContext2D,
  data: LabelDrawData,
  settings: Settings<Attributes, Attributes, Attributes>,
): void {
  // Light-theme hover ring around the node (NO background box — sigma's
  // default drawDiscNodeHover paints a box that clashes; we draw a ring only).
  context.beginPath();
  context.arc(data.x, data.y, data.size + 3, 0, Math.PI * 2);
  context.lineWidth = 2;
  context.strokeStyle = "#1f2328"; // --syn-text — visible on white canvas
  context.stroke();

  // Then draw the halo'd label (reuse the label drawer)
  drawHaloNodeLabel(context, data, settings);
}

// ─── Node tooltip ─────────────────────────────────────────────────────────────

interface TooltipProps {
  nodeId: string;
  position: { x: number; y: number };
  neighborCount: number;
  onClose: () => void;
}

const NodeTooltip: React.FC<TooltipProps> = ({ nodeId, position, neighborCount, onClose }) => {
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
        <span style={{ color: "var(--syn-text-muted)", fontSize: 12 }}>Loading...</span>
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
            {neighborCount} connection{neighborCount !== 1 ? "s" : ""}
          </div>
        </>
      ) : (
        <span style={{ color: "var(--syn-text-muted)", fontSize: 12 }}>Page not found</span>
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
        aria-label="Close tooltip"
      >
        x
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
}

const GraphLegend: React.FC<GraphLegendProps> = ({ colorMode, communities }) => {
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
              <span style={{ fontSize: 11, color: "var(--syn-text)", textTransform: "capitalize" }}>{type}</span>
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
                  style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}
                  data-testid={`community-legend-item-${c.id}`}
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
}

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

  // Color-mode toggle: "type" (default) or "community" (llm_wiki pattern)
  const [colorMode, setColorMode] = useState<ColorMode>("type");

  // Tooltip state (React state — triggers re-render to show/hide tooltip)
  const [tooltip, setTooltip] = useState<TooltipState | null>(null);
  // Aria-live announcement text
  const [announcement, setAnnouncement] = useState<string>("");

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

  // ── Build graphology graph and mount sigma ────────────────────────────────

  useEffect(() => {
    if (!containerRef.current) return;
    if (nodes.length === 0) return;

    // Build graphology graph from precomputed coords.
    // I2: buildGraphologyGraph sets x/y directly from server — no layout called.
    const rawGraph = buildGraphologyGraph(nodes, edges);

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
      });
    });

    rawGraph.forEachEdge((_edgeKey, attrs, source, target) => {
      if (!sigmaGraph.hasEdge(source, target)) {
        sigmaGraph.addEdge(source, target, {
          weight: attrs["weight"] as number,
          color: attrs["color"] as string,
          size: attrs["size"] as number,
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
    const sigmaSettings: Partial<Settings<Attributes, Attributes, Attributes>> = {
      // Label rendering — light theme: dark near-black labels (--syn-text)
      labelFont: "Inter, system-ui, sans-serif",
      labelSize: 13,
      labelWeight: "600",
      labelColor: { color: "#1f2328" }, // --syn-text
      labelDensity: 0.4,
      labelGridCellSize: 80,
      labelRenderedSizeThreshold: 14,

      // Custom halo drawers (AAA contrast)
      defaultDrawNodeLabel: drawHaloNodeLabel,
      defaultDrawNodeHover: drawHaloNodeHover,

      // Edge events required for edgeReducer hover detection
      enableEdgeEvents: true,

      // zIndex enables per-node z ordering in reducers
      zIndex: true,

      // Camera bounds
      minCameraRatio: 0.1,
      maxCameraRatio: 4,

      // ── nodeReducer: Obsidian hover-dim ─────────────────────────────────
      nodeReducer(node: string, data: Attributes): Partial<NodeDisplayData> {
        const res: Partial<NodeDisplayData> & Attributes = { ...data };

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
            // All other nodes: dim (washed-out light gray, hide label)
            res["label"] = "";
            res["color"] = "#c7ccd4"; // light-theme dim: close to --syn-border
            res["zIndex"] = 0;
          }
        }

        return res as Partial<NodeDisplayData>;
      },

      // ── edgeReducer: Obsidian hover-dim ─────────────────────────────────
      edgeReducer(edge: string, data: Attributes) {
        const res: Attributes = { ...data };

        if (hoverState.hoveredNode !== null) {
          const [src, tgt] = sigmaGraph.extremities(edge);
          const srcRelevant =
            src === hoverState.hoveredNode ||
            (hoverState.hoveredNeighbors?.has(src) ?? false);
          const tgtRelevant =
            tgt === hoverState.hoveredNode ||
            (hoverState.hoveredNeighbors?.has(tgt) ?? false);

          if (!srcRelevant || !tgtRelevant) {
            // Non-incident: hide entirely (Obsidian dim)
            res["hidden"] = true;
          } else {
            // Incident to hovered node: darker on light background (--syn-text-muted, thicker)
            res["color"] = "rgba(89,99,110,0.85)"; // --syn-text-muted #59636e
            res["size"] = ((data["size"] as number | undefined) ?? 1) * 2;
          }
        }

        return res;
      },
    };

    // Instantiate sigma — creates ONE WebGL <canvas> inside containerRef (I4)
    // I2: sigma renders FIXED precomputed positions. No layout algorithm. No rAF physics.
    const sigma = new Sigma(sigmaGraph, containerRef.current, sigmaSettings);
    sigmaRef.current = sigma;

    // ── Drag state (closed-over plain object — no React re-render during drag) ─
    const dragState: DragState = { draggedNode: null, hasMoved: false };

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
    sigma.on("downNode", ({ node }) => {
      dragState.draggedNode = node;
      dragState.hasMoved = false;
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
    // Rebuild sigma when graph data or color-mode changes (colorMode switches palette)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges, colorMode]);

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

    setAnnouncement(
      `Selected: ${title}. Type: ${type}. ${neighborCount} neighbor${neighborCount !== 1 ? "s" : ""}.`,
    );

    // Trigger a refresh so sigma re-applies reducers with updated selectedNode
    sigmaRef.current.refresh({ skipIndexation: true });
  }, [selectedNodeId]);

  const handleTooltipClose = useCallback(() => {
    setSelectedNodeId(null);
    setTooltip(null);
    setAnnouncement("");
  }, [setSelectedNodeId]);

  return (
    // I4: this container holds sigma's single <canvas> + a handful of overlay divs.
    // Total DOM nodes inside: <div#sigma-container> + <canvas> + aria-live + overlays = ~10 → well under 20.
    <div
      id="graph-root"
      role="application"
      aria-label="Knowledge graph"
      style={{ position: "relative", width: "100%", height: "100%", overflow: "hidden", background: "var(--syn-bg)" }}
    >
      {/* sigma mounts ONE WebGL <canvas> here — I4.
          Background is var(--syn-bg) (white) to match the llm_wiki light theme.
          sigma inherits the container background for its WebGL clear color. */}
      <div
        id="sigma-container"
        ref={containerRef}
        style={{ width: "100%", height: "100%", background: "var(--syn-bg)" }}
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
        <span style={{ fontSize: 10, color: "var(--syn-text-muted)", marginRight: 2, letterSpacing: "0.05em" }}>
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
      <GraphLegend colorMode={colorMode} communities={communities} />

      {/* Tooltip — conditional, at most 1 visible at a time */}
      {tooltip !== null && (
        <NodeTooltip
          nodeId={tooltip.nodeId}
          position={tooltip.position}
          neighborCount={tooltip.neighborCount}
          onClose={handleTooltipClose}
        />
      )}
    </div>
  );
};

export default GraphViewer;
