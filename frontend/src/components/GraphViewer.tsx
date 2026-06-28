/**
 * GraphViewer.tsx — sigma.js WebGL knowledge-graph viewer.
 *
 * INVARIANT I2 (ADR-0015 HARD RULE — P0 block):
 *   This component MUST NOT import or call any client-side layout algorithm.
 *   Forbidden: graphology-layout-forceatlas2, d3-force, @antv/layout,
 *              any rAF loop that mutates node x/y.
 *   Node positions come EXCLUSIVELY from the Zustand store (which reflects
 *   the server's precomputed FA2 coords from GET /graph).
 *
 * INVARIANT I4 (ADR-0015 §5):
 *   sigma draws ALL nodes/edges in a SINGLE <canvas> (WebGL).
 *   DOM node count in the graph container is < 20, regardless of graph size.
 *   No editor (CodeMirror) is rendered here — that is v0.4 scope.
 *
 * INVARIANT I3 (ADR-0015 §3):
 *   This component subscribes via typed selectors with shallow equality only.
 *   No whole-store subscription.
 *
 * G2 by construction: no layout → no main-thread long task > 50ms.
 * G4 by construction: single WebGL canvas → bounded DOM (<20 nodes in container).
 */

import React, { useCallback, useEffect, useRef, useState } from "react";
import Sigma from "sigma";
import type { Attributes } from "graphology-types";
import { buildGraphologyGraph } from "../api/graphTransform";
import { fetchGraph, fetchPageDetail } from "../api/graphClient";
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

// ─── Type colors (legend) ─────────────────────────────────────────────────────

const TYPE_COLORS: Record<string, string> = {
  concept: "#58a6ff",
  entity: "#3fb950",
  source: "#f78166",
  synthesis: "#bc8cff",
  comparison: "#ffa657",
};

const DEFAULT_NODE_COLOR = "#8b949e";
const DEFAULT_EDGE_COLOR = "#30363d";

function colorForType(type: string | null): string {
  if (type === null) return DEFAULT_NODE_COLOR;
  return TYPE_COLORS[type] ?? DEFAULT_NODE_COLOR;
}

// ─── Node tooltip ─────────────────────────────────────────────────────────────

interface TooltipProps {
  nodeId: string;
  position: { x: number; y: number };
  onClose: () => void;
}

const NodeTooltip: React.FC<TooltipProps> = ({ nodeId, position, onClose }) => {
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
      aria-live="polite"
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
        ×
      </button>
    </div>
  );
};

// ─── Legend ───────────────────────────────────────────────────────────────────

const GraphLegend: React.FC = () => (
  <div
    style={{
      position: "absolute",
      bottom: 16,
      left: 16,
      background: "rgba(13,17,23,0.85)",
      border: "1px solid #30363d",
      borderRadius: 6,
      padding: "8px 12px",
      zIndex: 5,
      pointerEvents: "none",
    }}
    aria-label="Graph node type legend"
  >
    <div style={{ fontSize: 10, color: "#8b949e", marginBottom: 6, letterSpacing: "0.08em" }}>
      NODE TYPES
    </div>
    {Object.entries(TYPE_COLORS).map(([type, color]) => (
      <div
        key={type}
        style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 3 }}
      >
        <span
          style={{ width: 8, height: 8, borderRadius: "50%", background: color, flexShrink: 0 }}
        />
        <span style={{ fontSize: 11, color: "#e6edf3", textTransform: "capitalize" }}>{type}</span>
      </div>
    ))}
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <span
        style={{
          width: 8,
          height: 8,
          borderRadius: "50%",
          background: DEFAULT_NODE_COLOR,
          flexShrink: 0,
        }}
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

// ─── Main GraphViewer ──────────────────────────────────────────────────────────

interface TooltipState {
  nodeId: string;
  position: { x: number; y: number };
}

/**
 * GraphViewer — renders the Synapse knowledge graph using sigma.js (WebGL).
 *
 * Single route, read-only viewer. No editor, no chat, no provider selector (v0.4).
 *
 * DOM structure (I4 — <20 nodes in container):
 *   <div#graph-root>          ← container
 *     <div#sigma-container>   ← sigma mounts its canvas here
 *       <canvas>              ← ONE WebGL canvas (sigma owns this)
 *     </div>
 *     <StatusBar />           ← absolute overlay, no new graph DOM nodes
 *     <GraphLegend />         ← absolute overlay
 *     <NodeTooltip />         ← conditional, single element
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

  // sigma container ref — sigma will mount ONE <canvas> inside this div (I4)
  const containerRef = useRef<HTMLDivElement>(null);
  // sigma instance ref — kept outside React state to avoid re-render on mount
  const sigmaRef = useRef<Sigma<Attributes, Attributes, Attributes> | null>(null);

  // Tooltip state
  const [tooltip, setTooltip] = useState<TooltipState | null>(null);

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

  // ── Build graphology graph and render with sigma ──────────────────────────

  useEffect(() => {
    if (!containerRef.current) return;
    if (nodes.length === 0) return;

    // Build graphology graph from precomputed coords.
    // I2: buildGraphologyGraph sets x/y directly from server — no layout called.
    // Color is embedded as a node attribute so sigma picks it up without a nodeReducer.
    const rawGraph = buildGraphologyGraph(nodes, edges);

    // Build an Attributes-typed graphology graph for sigma
    // by copying all node/edge attributes (including server x/y) into a plain Attributes graph.
    // This avoids the generic mismatch between SynapseGraph<NodeAttributes> and Sigma<Attributes>.
    const Graph = rawGraph.constructor as new (opts: {
      multi: boolean;
      type: string;
    }) => import("graphology").default;
    const sigmaGraph = new Graph({ multi: false, type: "undirected" });

    rawGraph.forEachNode((nodeKey, attrs) => {
      const nodeType = attrs["type"] as string | null | undefined;
      sigmaGraph.addNode(nodeKey, {
        x: attrs["x"] as number,
        y: attrs["y"] as number,
        label: attrs["label"] as string,
        size: attrs["size"] as number,
        // Embed color as a sigma display attribute — this avoids needing nodeReducer
        color: colorForType(nodeType ?? null),
      });
    });

    rawGraph.forEachEdge((_edgeKey, attrs, source, target) => {
      if (!sigmaGraph.hasEdge(source, target)) {
        sigmaGraph.addEdge(source, target, {
          weight: attrs["weight"] as number,
          color: DEFAULT_EDGE_COLOR,
          size: 1,
        });
      }
    });

    // Destroy previous sigma instance
    if (sigmaRef.current) {
      sigmaRef.current.kill();
      sigmaRef.current = null;
    }

    // Instantiate sigma — it creates ONE WebGL <canvas> inside containerRef (I4)
    // I2: sigma renders the FIXED precomputed positions from sigmaGraph.
    //     No layout algorithm is called here. No rAF physics loop is started.
    const sigma = new Sigma(sigmaGraph, containerRef.current, {
      renderEdgeLabels: false,
      defaultNodeColor: DEFAULT_NODE_COLOR,
      defaultEdgeColor: DEFAULT_EDGE_COLOR,
    });

    sigmaRef.current = sigma;

    // Node click → show tooltip (reads clicked coords from the event, not from layout)
    sigma.on("clickNode", ({ node, event }) => {
      setSelectedNodeId(node);
      setTooltip({
        nodeId: node,
        position: { x: event.x, y: event.y },
      });
    });

    // Stage click → deselect
    sigma.on("clickStage", () => {
      setSelectedNodeId(null);
      setTooltip(null);
    });

    // Fit camera to graph on initial render
    sigma.getCamera().animatedReset();

    return () => {
      sigma.kill();
      sigmaRef.current = null;
    };
    // Rebuild sigma only when the graph data changes (new fetch result)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges]);

  // Sync tooltip close with deselect
  const handleTooltipClose = useCallback(() => {
    setSelectedNodeId(null);
    setTooltip(null);
  }, [setSelectedNodeId]);

  // Refresh sigma highlight when selection changes
  useEffect(() => {
    if (sigmaRef.current) {
      sigmaRef.current.refresh();
    }
  }, [selectedNodeId]);

  return (
    // I4: this container holds sigma's single <canvas> + a handful of overlay divs
    // Total DOM nodes inside: <div#sigma-container> + <canvas> + 3 overlays = ~6 → well under 20
    <div
      id="graph-root"
      style={{ position: "relative", width: "100%", height: "100%", overflow: "hidden" }}
    >
      {/* sigma mounts ONE <canvas> (WebGL) here — I4 */}
      <div
        id="sigma-container"
        ref={containerRef}
        style={{ width: "100%", height: "100%" }}
        aria-label="Knowledge graph canvas"
      />

      {/* Status bar overlay — absolute positioned */}
      <StatusBar />

      {/* Legend overlay — absolute positioned */}
      <GraphLegend />

      {/* Tooltip — conditional, at most 1 visible at a time */}
      {tooltip !== null && (
        <NodeTooltip
          nodeId={tooltip.nodeId}
          position={tooltip.position}
          onClose={handleTooltipClose}
        />
      )}
    </div>
  );
};

export default GraphViewer;
