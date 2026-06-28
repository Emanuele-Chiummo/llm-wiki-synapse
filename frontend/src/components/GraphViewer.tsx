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
 *   - Label contrast #e6edf3 on #0d1117 ≈ 16:1 (AAA)
 *   - CVD-safe type palette with redundant encoding (name in legend + tooltip text)
 *   - prefers-reduced-motion respected for all camera animations
 */

import React, { useCallback, useEffect, useRef, useState } from "react";
import Sigma from "sigma";
import type { Attributes } from "graphology-types";
import type { Settings } from "sigma/settings";
import type { NodeDisplayData, PartialButFor } from "sigma/types";
import { buildGraphologyGraph } from "../api/graphTransform";
import { fetchGraph, fetchPageDetail, patchNodePosition } from "../api/graphClient";
import type { PageDetail } from "../api/types";
import {
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

const TYPE_COLORS: Record<string, string> = {
  concept: "#58a6ff", // blue
  entity: "#3fb950", // green
  source: "#ffa657", // orange
  synthesis: "#d2a8ff", // purple
  comparison: "#f2cc60", // yellow
};

const DEFAULT_NODE_COLOR = "#8b949e";

function colorForType(type: string | null): string {
  if (type === null) return DEFAULT_NODE_COLOR;
  return TYPE_COLORS[type] ?? DEFAULT_NODE_COLOR;
}

/**
 * Brighten a hex color by mixing it 40% toward white (#ffffff).
 * Used in nodeReducer to make neighbor nodes pop against the dimmed background.
 * Handles both 3-char (#rgb) and 6-char (#rrggbb) hex; falls back to input on parse error.
 */
function brightenColor(hex: string): string {
  const clean = hex.startsWith("#") ? hex.slice(1) : hex;
  const full = clean.length === 3 ? clean.replace(/./g, (c) => c + c) : clean;
  if (full.length !== 6) return hex;

  const r = parseInt(full.slice(0, 2), 16);
  const g = parseInt(full.slice(2, 4), 16);
  const b = parseInt(full.slice(4, 6), 16);

  if (isNaN(r) || isNaN(g) || isNaN(b)) return hex;

  const mix = 0.4; // 40% toward white
  const br = Math.round(r + (255 - r) * mix);
  const bg = Math.round(g + (255 - g) * mix);
  const bb = Math.round(b + (255 - b) * mix);

  return `#${br.toString(16).padStart(2, "0")}${bg.toString(16).padStart(2, "0")}${bb.toString(16).padStart(2, "0")}`;
}

// ─── Halo label drawer (accessible, AAA contrast) ────────────────────────────
// sigma v3 has no built-in halo; we override defaultDrawNodeLabel.
// Dark halo (#0d1117, lineWidth 3) then light fill (#e6edf3) — ~16:1 contrast.

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

  // Halo stroke (dark) — improves readability on any background
  context.strokeStyle = "#0d1117";
  context.lineWidth = 3;
  context.lineJoin = "round";
  context.strokeText(data.label, x, y);

  // Label fill (light)
  context.fillStyle = "#e6edf3";
  context.fillText(data.label, x, y);
}

// ─── Halo hover drawer ────────────────────────────────────────────────────────
// Draws a highlight ring around the hovered node, then the halo'd label.

function drawHaloNodeHover(
  context: CanvasRenderingContext2D,
  data: LabelDrawData,
  settings: Settings<Attributes, Attributes, Attributes>,
): void {
  // Dark-theme hover ring around the node (NO white background box — sigma's
  // default drawDiscNodeHover paints a light rect that clashes with the dark UI).
  context.beginPath();
  context.arc(data.x, data.y, data.size + 3, 0, Math.PI * 2);
  context.lineWidth = 2;
  context.strokeStyle = "#e6edf3";
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
      style={{
        position: "absolute",
        left: position.x + 12,
        top: position.y - 8,
        background: "#161b22",
        border: "1px solid #30363d",
        borderRadius: 6,
        padding: "8px 12px",
        maxWidth: 240,
        zIndex: 10,
        boxShadow: "0 4px 12px rgba(0,0,0,0.5)",
        pointerEvents: "none",
      }}
      role="tooltip"
    >
      {fetching ? (
        <span style={{ color: "#8b949e", fontSize: 12 }}>Loading...</span>
      ) : detail !== null ? (
        <>
          <div style={{ fontWeight: 600, fontSize: 13, color: "#e6edf3", marginBottom: 4 }}>
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
          <div style={{ fontSize: 11, color: "#8b949e", marginTop: 4 }}>
            {neighborCount} connection{neighborCount !== 1 ? "s" : ""}
          </div>
        </>
      ) : (
        <span style={{ color: "#8b949e", fontSize: 12 }}>Page not found</span>
      )}
      <button
        style={{
          position: "absolute",
          top: 4,
          right: 6,
          background: "none",
          border: "none",
          color: "#8b949e",
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
// CVD-safe: shows color swatch AND type name (redundant encoding, WCAG 1.4.1).

const GraphLegend: React.FC = () => (
  <div
    style={{
      position: "absolute",
      bottom: 16,
      left: 16,
      background: "rgba(13,17,23,0.90)",
      border: "1px solid #30363d",
      borderRadius: 6,
      padding: "8px 12px",
      zIndex: 5,
      pointerEvents: "none",
      userSelect: "none",
    }}
    aria-label="Graph node type legend"
  >
    <div
      style={{
        fontSize: 10,
        color: "#8b949e",
        marginBottom: 6,
        letterSpacing: "0.08em",
        fontWeight: 600,
      }}
    >
      NODE TYPES
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
            boxShadow: `0 0 0 1px rgba(0,0,0,0.3)`,
          }}
          aria-hidden="true"
        />
        {/* Redundant encoding: type NAME shown alongside color (WCAG 1.4.1) */}
        <span style={{ fontSize: 11, color: "#e6edf3", textTransform: "capitalize" }}>{type}</span>
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
        }}
        aria-hidden="true"
      />
      <span style={{ fontSize: 11, color: "#8b949e" }}>other</span>
    </div>
  </div>
);

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
          background: "#da3633",
          color: "#fff",
          borderRadius: 4,
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
        style={{
          position: "absolute",
          top: 12,
          left: "50%",
          transform: "translateX(-50%)",
          background: "#161b22",
          border: "1px solid #30363d",
          borderRadius: 4,
          padding: "4px 12px",
          fontSize: 12,
          color: "#8b949e",
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
      style={{
        position: "absolute",
        top: 12,
        right: 12,
        background: "rgba(13,17,23,0.85)",
        border: "1px solid #30363d",
        borderRadius: 4,
        padding: "3px 8px",
        fontSize: 11,
        color: "#8b949e",
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
        <span style={{ color: cacheStatus === "hit" ? "#3fb950" : "#ffa657" }}>
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
  // I3: typed selectors — never subscribe to whole store
  const nodes = useGraphStore(selectNodes);
  const edges = useGraphStore(selectEdges);
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
        setGraph(data.nodes, data.edges, data.data_version, cacheStatus);
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
      sigmaGraph.addNode(nodeKey, {
        x: attrs["x"] as number,
        y: attrs["y"] as number,
        label: attrs["label"] as string,
        size: attrs["size"] as number,
        color: colorForType(nodeType ?? null),
        // Store degree for reducers
        degree: attrs["degree"] as number,
        // Store type for reducers
        nodeType: nodeType ?? null,
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
      // Label rendering
      labelFont: "Inter, system-ui, sans-serif",
      labelSize: 13,
      labelWeight: "600",
      labelColor: { color: "#e6edf3" },
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
            // Neighbor: show label, raised z, subtly brightened color so cluster pops
            res["forceLabel"] = true;
            res["zIndex"] = 1;
            // Mix toward white to brighten while preserving hue
            res["color"] = brightenColor((data["color"] as string | undefined) ?? DEFAULT_NODE_COLOR);
          } else {
            // All other nodes: dim (dark color, hide label)
            res["label"] = "";
            res["color"] = "#2a2f37";
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
            // Incident to hovered node: light up clearly (bright + thicker)
            res["color"] = "rgba(201,209,217,0.9)";
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
    // Rebuild sigma only when graph data changes (new fetch result)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges]);

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
    // Total DOM nodes inside: <div#sigma-container> + <canvas> + aria-live + 3 overlays = ~7 → well under 20.
    <div
      id="graph-root"
      role="application"
      aria-label="Knowledge graph"
      style={{ position: "relative", width: "100%", height: "100%", overflow: "hidden" }}
    >
      {/* sigma mounts ONE WebGL <canvas> here — I4 */}
      <div
        id="sigma-container"
        ref={containerRef}
        style={{ width: "100%", height: "100%" }}
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

      {/* Status bar overlay */}
      <StatusBar />

      {/* Legend overlay — CVD-safe: name + color swatch */}
      <GraphLegend />

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
