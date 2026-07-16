/**
 * PreviewPanel.tsx — "Connections" right-panel: neighbor mini-graph + linked-from list.
 *
 * WS-F v1.7.0 redesign: the panel is now a "Connections" view that shows:
 *   1. A mini SVG neighborhood graph using precomputed server coordinates (I2 compliant —
 *      NO client-side layout is computed; we render FA2 coords already in the store).
 *   2. "Linked from" — inbound edges (target === selectedNodeId), with type dot + title + weight.
 *   3. "Links to"    — outbound edges (source === selectedNodeId), brief.
 *
 * The delete affordance (CascadeDeleteModal, F13) is retained in the header.
 *
 * INVARIANT I2: mini-graph renders server-precomputed (x,y) coordinates; no force-layout.
 * INVARIANT I3: subscribes via typed selectors + useShallow for collections.
 *
 * Degrade-safe: when the graph store is empty (graph not yet loaded), the "Connections"
 * header still renders and both lists show an empty state (no crash).
 */

import { useMemo, useState, useCallback } from "react";
import { useShallow } from "zustand/react/shallow";
import { useTranslation } from "react-i18next";
import { useGraphStore, selectNodes, selectEdges } from "../../store/graphStore";
import { useAppStore, selectSelectedNodeId } from "../../store/appStore";
import type { GraphNode, GraphEdge, CascadeDeleteResult } from "../../api/types";
import { CascadeDeleteModal } from "../wiki/CascadeDeleteModal";
import { pageTypeCssColor } from "../../utils/pageTypeVisuals";

// ─── Type colour helper ───────────────────────────────────────────────────────

function typeColorVar(type: string | null): string {
  return pageTypeCssColor(type);
}

// ─── Mini neighborhood SVG ────────────────────────────────────────────────────
// Renders a compact SVG visualization of the selected node and its immediate
// neighbors, using precomputed FA2 coordinates from the graph store.
// I2: no layout is computed here — we only transform stored (x, y) to screen space.

const MINI_W = 230;
const MINI_H = 148;
const MINI_PAD = 22;

/** Normalize node coords to fit within the mini-graph viewport. */
function normalizeCoords(nodeList: GraphNode[]): Map<string, { x: number; y: number }> {
  if (nodeList.length === 0) return new Map();
  if (nodeList.length === 1) {
    const solo = nodeList[0];
    if (!solo) return new Map();
    return new Map([[solo.id, { x: MINI_W / 2, y: MINI_H / 2 }]]);
  }
  const xs = nodeList.map((n) => n.x);
  const ys = nodeList.map((n) => n.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const rangeX = maxX - minX || 1;
  const rangeY = maxY - minY || 1;
  const scale = Math.min((MINI_W - 2 * MINI_PAD) / rangeX, (MINI_H - 2 * MINI_PAD) / rangeY);
  // Center the scaled content in the viewport
  const scaledW = rangeX * scale;
  const scaledH = rangeY * scale;
  const offsetX = MINI_PAD + (MINI_W - 2 * MINI_PAD - scaledW) / 2;
  const offsetY = MINI_PAD + (MINI_H - 2 * MINI_PAD - scaledH) / 2;
  return new Map(
    nodeList.map((n) => [
      n.id,
      {
        x: offsetX + (n.x - minX) * scale,
        y: offsetY + (n.y - minY) * scale,
      },
    ]),
  );
}

interface MiniGraphProps {
  selectedNode: GraphNode;
  neighbors: GraphNode[];
  incidentEdges: GraphEdge[];
  coordMap: Map<string, { x: number; y: number }>;
}

function MiniGraph({ selectedNode, neighbors, incidentEdges, coordMap }: MiniGraphProps) {
  const selCoord = coordMap.get(selectedNode.id);
  if (!selCoord) return null;

  return (
    <div
      style={{
        height: MINI_H,
        border: "1px solid var(--syn-border)",
        borderRadius: "var(--syn-radius-md)",
        background: "var(--syn-bg)",
        overflow: "hidden",
        position: "relative",
      }}
      aria-hidden="true"
    >
      <svg
        viewBox={`0 0 ${MINI_W} ${MINI_H}`}
        width="100%"
        height="100%"
        style={{ display: "block" }}
      >
        {/* Edge lines */}
        {incidentEdges.map((edge, i) => {
          const aId = edge.source === selectedNode.id ? edge.target : edge.source;
          const a = coordMap.get(aId);
          if (!a) return null;
          const isInbound = edge.target === selectedNode.id;
          return (
            <line
              key={`${edge.source}-${edge.target}-${i}`}
              x1={selCoord.x}
              y1={selCoord.y}
              x2={a.x}
              y2={a.y}
              stroke={isInbound ? "var(--syn-accent)" : "var(--syn-border)"}
              strokeWidth={isInbound ? 1.6 : 1.2}
              opacity={isInbound ? 0.55 : 0.45}
            />
          );
        })}
        {/* Neighbor nodes */}
        {neighbors.map((n) => {
          const coord = coordMap.get(n.id);
          if (!coord) return null;
          const color = typeColorVar(n.type);
          return <circle key={n.id} cx={coord.x} cy={coord.y} r={5} fill={color} opacity={0.85} />;
        })}
        {/* Selected node — larger, accent-colored with halo */}
        <circle
          cx={selCoord.x}
          cy={selCoord.y}
          r={14}
          fill="none"
          stroke="var(--syn-accent)"
          strokeWidth={1.4}
          opacity={0.3}
        />
        <circle cx={selCoord.x} cy={selCoord.y} r={9} fill="var(--syn-accent)" />
      </svg>
    </div>
  );
}

// ─── Connection list item ────────────────────────────────────────────────────

interface ConnItemProps {
  node: GraphNode | undefined;
  edge: GraphEdge;
  onSelect: (id: string) => void;
}

function ConnItem({ node, edge, onSelect }: ConnItemProps) {
  const otherId = node?.id ?? (edge.source === node?.id ? edge.target : edge.source);
  const color = typeColorVar(node?.type ?? null);
  const title = node?.title ?? otherId;

  return (
    <button
      type="button"
      onClick={() => onSelect(otherId)}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 9,
        width: "100%",
        padding: "7px 8px",
        border: "none",
        background: "transparent",
        cursor: "pointer",
        borderRadius: "var(--syn-radius-sm)",
        textAlign: "left",
        fontSize: 12.5,
        color: "var(--syn-text-muted)",
        transition: "background 0.12s ease",
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLButtonElement).style.background = "var(--syn-bg)";
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLButtonElement).style.background = "transparent";
      }}
    >
      {/* Type dot */}
      <span
        aria-hidden="true"
        style={{
          width: 7,
          height: 7,
          borderRadius: 2,
          background: color,
          flexShrink: 0,
        }}
      />
      {/* Title */}
      <span
        style={{
          flex: 1,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          color: "var(--syn-text)",
        }}
      >
        {title}
      </span>
      {/* Weight — mono count */}
      <span
        aria-hidden="true"
        style={{
          fontFamily: "var(--syn-font-mono, monospace)",
          fontSize: 10,
          color: "var(--syn-text-dim)",
          flexShrink: 0,
        }}
      >
        {edge.weight.toFixed(1)}
      </span>
    </button>
  );
}

// ─── Section heading ─────────────────────────────────────────────────────────

function ConnSectionHead({ label, count }: { label: string; count: number }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        marginBottom: 4,
      }}
    >
      <span
        style={{
          fontSize: 12,
          fontWeight: 640,
          color: "var(--syn-text)",
        }}
      >
        {label}
      </span>
      <span
        style={{
          fontFamily: "var(--syn-font-mono, monospace)",
          fontSize: 11,
          color: "var(--syn-text-dim)",
          marginLeft: "auto",
        }}
      >
        {count}
      </span>
    </div>
  );
}

// ─── Component ────────────────────────────────────────────────────────────────

export function PreviewPanel() {
  const { t } = useTranslation();
  const selectedNodeId = useAppStore(selectSelectedNodeId);
  const nodes = useGraphStore(useShallow(selectNodes));
  const edges = useGraphStore(useShallow(selectEdges));
  const setSelectedNodeId = useAppStore((s) => s.setSelectedNodeId);

  // ── Cascade-delete modal state ─────────────────────────────────────────────
  const [deleteModalOpen, setDeleteModalOpen] = useState(false);

  const handleDeleteSuccess = useCallback(
    (_result: CascadeDeleteResult) => {
      setSelectedNodeId(null);
      setDeleteModalOpen(false);
    },
    [setSelectedNodeId],
  );

  // Selected node
  const selectedNode = useMemo<GraphNode | null>(
    () => nodes.find((n) => n.id === selectedNodeId) ?? null,
    [nodes, selectedNodeId],
  );

  // Node lookup map
  const nodeById = useMemo<Map<string, GraphNode>>(() => {
    const m = new Map<string, GraphNode>();
    for (const n of nodes) m.set(n.id, n);
    return m;
  }, [nodes]);

  // Split incident edges into inbound (← ) and outbound (→)
  const { inboundEdges, outboundEdges } = useMemo<{
    inboundEdges: GraphEdge[];
    outboundEdges: GraphEdge[];
  }>(() => {
    if (!selectedNodeId) return { inboundEdges: [], outboundEdges: [] };
    const inb: GraphEdge[] = [];
    const out: GraphEdge[] = [];
    for (const e of edges) {
      if (e.target === selectedNodeId) inb.push(e);
      else if (e.source === selectedNodeId) out.push(e);
    }
    // Sort by weight desc for relevance order
    inb.sort((a, b) => b.weight - a.weight);
    out.sort((a, b) => b.weight - a.weight);
    return { inboundEdges: inb, outboundEdges: out };
  }, [edges, selectedNodeId]);

  const incidentEdges = useMemo(
    () => [...inboundEdges, ...outboundEdges],
    [inboundEdges, outboundEdges],
  );

  // Neighborhood nodes (selected + neighbors) for the mini-graph
  const neighborNodes = useMemo<GraphNode[]>(() => {
    if (!selectedNode) return [];
    const neighborIds = new Set(
      incidentEdges.map((e) => (e.source === selectedNodeId ? e.target : e.source)),
    );
    return nodes.filter((n) => neighborIds.has(n.id));
  }, [nodes, selectedNode, selectedNodeId, incidentEdges]);

  const allMiniNodes = useMemo<GraphNode[]>(
    () => (selectedNode ? [selectedNode, ...neighborNodes.slice(0, 8)] : []),
    [selectedNode, neighborNodes],
  );

  // Precomputed coord map for the mini-graph (I2: uses server FA2 coords directly)
  const coordMap = useMemo(() => normalizeCoords(allMiniNodes), [allMiniNodes]);

  const handleSelectPage = useCallback(
    (pageId: string) => {
      setSelectedNodeId(pageId);
    },
    [setSelectedNodeId],
  );

  // ── Empty state ────────────────────────────────────────────────────────────

  if (!selectedNode) {
    return (
      <div
        className="preview-panel preview-panel--empty"
        data-testid="preview-panel"
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
          color: "var(--syn-text-dim)",
          fontSize: 13,
          padding: 16,
          textAlign: "center",
          gap: 8,
        }}
      >
        <span aria-hidden="true" style={{ fontSize: 32, opacity: 0.3 }}>
          &#9728;
        </span>
        <p style={{ margin: 0 }}>
          Select a node in the graph or a page in the tree to see details.
        </p>
      </div>
    );
  }

  // ── Populated state ────────────────────────────────────────────────────────

  const colorVar = typeColorVar(selectedNode.type);

  return (
    <div
      className="preview-panel"
      data-testid="preview-panel"
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
        background: "var(--syn-bg-soft)",
      }}
    >
      {/* Header — type badge + title + delete affordance */}
      <header
        className="preview-panel__header"
        style={{
          padding: "14px 15px 12px",
          borderBottom: "1px solid var(--syn-border)",
          flexShrink: 0,
          background: "var(--syn-bg-soft)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
          {/* Type badge */}
          <span
            aria-label={`Type: ${selectedNode.type ?? "other"}`}
            style={{
              fontFamily: "var(--syn-font-mono, monospace)",
              fontSize: 11,
              fontWeight: 600,
              letterSpacing: "0.02em",
              textTransform: "lowercase",
              color: colorVar,
              background: `color-mix(in srgb, ${colorVar} 12%, var(--syn-bg-soft) 88%)`,
              border: `1px solid color-mix(in srgb, ${colorVar} 25%, var(--syn-border) 75%)`,
              borderRadius: "var(--syn-radius-pill)",
              padding: "2px 8px 2px 6px",
              display: "inline-flex",
              alignItems: "center",
              gap: 5,
            }}
          >
            {/* Type dot */}
            <span
              aria-hidden="true"
              style={{
                width: 7,
                height: 7,
                borderRadius: 2,
                background: colorVar,
                flexShrink: 0,
              }}
            />
            {selectedNode.type ?? "other"}
          </span>

          {/* Delete affordance */}
          <button
            onClick={() => setDeleteModalOpen(true)}
            data-testid="preview-panel-delete-btn"
            aria-label={t("cascadeDelete.deleteButton")}
            title={t("cascadeDelete.deleteButton")}
            className="syn-toolbar-button"
            style={{ marginLeft: "auto" }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLButtonElement).style.color = "var(--syn-red)";
              (e.currentTarget as HTMLButtonElement).style.borderColor =
                "color-mix(in srgb, var(--syn-red) 40%, var(--syn-border) 60%)";
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLButtonElement).style.color = "";
              (e.currentTarget as HTMLButtonElement).style.borderColor = "";
            }}
          >
            {t("cascadeDelete.deleteButton")}
          </button>
        </div>

        <h2
          style={{
            margin: 0,
            fontSize: 14,
            fontWeight: 640,
            color: "var(--syn-text)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            letterSpacing: "-0.01em",
          }}
          title={selectedNode.title}
        >
          {selectedNode.title}
        </h2>
      </header>

      {/* Two-step cascade-delete modal (F13) */}
      {deleteModalOpen && (
        <CascadeDeleteModal
          pageId={selectedNode.id}
          pageTitle={selectedNode.title}
          onDeleted={handleDeleteSuccess}
          onCancel={() => setDeleteModalOpen(false)}
        />
      )}

      {/* Scrollable connections body */}
      <div
        className="preview-panel__body"
        style={{
          flex: 1,
          overflow: "auto",
          padding: "14px 13px 16px",
          display: "flex",
          flexDirection: "column",
          gap: 16,
        }}
      >
        {/* ── Mini neighborhood graph (I2: server coords only) ── */}
        {selectedNode && allMiniNodes.length > 0 && (
          <div>
            <ConnSectionHead label={t("previewPanel.neighborhood")} count={incidentEdges.length} />
            <MiniGraph
              selectedNode={selectedNode}
              neighbors={neighborNodes.slice(0, 8)}
              incidentEdges={incidentEdges.slice(0, 12)}
              coordMap={coordMap}
            />
          </div>
        )}

        {/* ── Linked from (inbound edges) ── */}
        {inboundEdges.length > 0 && (
          <div>
            <ConnSectionHead label={t("previewPanel.linkedFrom")} count={inboundEdges.length} />
            {inboundEdges.slice(0, 10).map((edge, i) => {
              const other = nodeById.get(edge.source);
              return (
                <ConnItem
                  key={`in-${edge.source}-${edge.target}-${i}`}
                  node={other}
                  edge={edge}
                  onSelect={handleSelectPage}
                />
              );
            })}
          </div>
        )}

        {/* ── Links to (outbound edges) ── */}
        {outboundEdges.length > 0 && (
          <div>
            <ConnSectionHead label={t("previewPanel.linksTo")} count={outboundEdges.length} />
            {outboundEdges.slice(0, 10).map((edge, i) => {
              const other = nodeById.get(edge.target);
              return (
                <ConnItem
                  key={`out-${edge.source}-${edge.target}-${i}`}
                  node={other}
                  edge={edge}
                  onSelect={handleSelectPage}
                />
              );
            })}
          </div>
        )}

        {/* ── Empty connections state ── */}
        {incidentEdges.length === 0 && (
          <p
            style={{
              margin: 0,
              fontSize: 12,
              color: "var(--syn-text-dim)",
              fontStyle: "italic",
            }}
          >
            {t("previewPanel.bodyHint")}
          </p>
        )}

        {/* Technical details (collapsed by default) */}
        <details style={{ marginTop: 4 }}>
          <summary
            style={{
              fontSize: 10,
              color: "var(--syn-text-dim)",
              cursor: "pointer",
              userSelect: "none",
              letterSpacing: "0.03em",
            }}
          >
            {t("previewPanel.technicalDetails")}
          </summary>
          <dl
            style={{
              margin: "6px 0 0",
              display: "grid",
              gridTemplateColumns: "auto 1fr",
              gap: "4px 12px",
              fontSize: 11,
            }}
          >
            <dt style={{ color: "var(--syn-text-muted)" }}>{t("previewPanel.connections")}</dt>
            <dd style={{ margin: 0, color: "var(--syn-text)" }}>
              {t("previewPanel.connectedTo", { count: incidentEdges.length })}
            </dd>
            <dt style={{ color: "var(--syn-text-muted)" }}>ID</dt>
            <dd
              style={{
                margin: 0,
                color: "var(--syn-text-dim)",
                fontFamily: "var(--syn-font-mono, monospace)",
                fontSize: 10,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
              title={selectedNode.id}
            >
              {selectedNode.id}
            </dd>
            <dt style={{ color: "var(--syn-text-muted)" }}>Position</dt>
            <dd
              style={{
                margin: 0,
                color: "var(--syn-text-dim)",
                fontFamily: "var(--syn-font-mono, monospace)",
                fontSize: 10,
              }}
            >
              {selectedNode.x.toFixed(2)}, {selectedNode.y.toFixed(2)}
            </dd>
          </dl>
        </details>
      </div>
    </div>
  );
}
