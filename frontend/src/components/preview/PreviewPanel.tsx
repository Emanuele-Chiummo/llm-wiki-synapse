/**
 * PreviewPanel.tsx — right-panel metadata + relationship view.
 *
 * Phase 1 (v0.4): shows metadata of the currently selected node pulled from
 * the Zustand store (nodes array). Relationship list (edges to/from the node)
 * is shown inline. Empty state when nothing is selected.
 *
 * v0.5 (F13): delete affordance added to the header — opens CascadeDeleteModal.
 *
 * INVARIANT I3: subscribes via typed selectors + useShallow for collections.
 *
 * All colors use --syn-* CSS variables (no hardcoded dark hex).
 */

import { useMemo, useState, useCallback } from "react";
import { useShallow } from "zustand/react/shallow";
import { useTranslation } from "react-i18next";
import { useGraphStore } from "../../store/graphStore";
import {
  selectSelectedNodeId,
  selectNodes,
  selectEdges,
} from "../../store/graphStore";
import type { GraphNode, GraphEdge, CascadeDeleteResult } from "../../api/types";
import { CascadeDeleteModal } from "../wiki/CascadeDeleteModal";

// ─── Type colour helper (uses --syn-type-* tokens) ───────────────────────────

function typeColorVar(type: string | null): string {
  const t = type ?? "other";
  return `var(--syn-type-${t}, var(--syn-type-other))`;
}

// ─── Component ────────────────────────────────────────────────────────────────

export function PreviewPanel() {
  const { t } = useTranslation();
  const selectedNodeId = useGraphStore(selectSelectedNodeId);
  const nodes = useGraphStore(useShallow(selectNodes));
  const edges = useGraphStore(useShallow(selectEdges));
  const setSelectedNodeId = useGraphStore((s) => s.setSelectedNodeId);

  // ── Cascade-delete modal state ─────────────────────────────────────────────
  const [deleteModalOpen, setDeleteModalOpen] = useState(false);

  const handleDeleteSuccess = useCallback(
    (_result: CascadeDeleteResult) => {
      // Remove the deleted page from selection and close the modal
      setSelectedNodeId(null);
      setDeleteModalOpen(false);
    },
    [setSelectedNodeId],
  );

  // Find selected node object
  const selectedNode = useMemo<GraphNode | null>(
    () => nodes.find((n) => n.id === selectedNodeId) ?? null,
    [nodes, selectedNodeId],
  );

  // Build node lookup for relationship list
  const nodeById = useMemo<Map<string, GraphNode>>(() => {
    const m = new Map<string, GraphNode>();
    for (const n of nodes) m.set(n.id, n);
    return m;
  }, [nodes]);

  // Edges connected to this node
  const incidentEdges = useMemo<GraphEdge[]>(() => {
    if (!selectedNodeId) return [];
    return edges.filter(
      (e) => e.source === selectedNodeId || e.target === selectedNodeId,
    );
  }, [edges, selectedNodeId]);

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
        <span
          aria-hidden="true"
          style={{ fontSize: 32, opacity: 0.3 }}
        >
          &#9728;
        </span>
        <p style={{ margin: 0 }}>Select a node in the graph or a page in the tree to see details.</p>
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
        background: "var(--syn-bg)",
      }}
    >
      {/* Header */}
      <header
        className="preview-panel__header"
        style={{
          padding: "12px 16px",
          borderBottom: "1px solid var(--syn-border)",
          flexShrink: 0,
          background: "var(--syn-bg-soft)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
          {/* Type badge */}
          <span
            aria-label={`Type: ${selectedNode.type ?? "other"}`}
            style={{
              fontSize: 11,
              fontWeight: 600,
              letterSpacing: "0.04em",
              textTransform: "uppercase",
              color: colorVar,
              background: `color-mix(in srgb, ${colorVar} 10%, var(--syn-bg) 90%)`,
              border: `1px solid color-mix(in srgb, ${colorVar} 30%, transparent 70%)`,
              borderRadius: "var(--syn-radius-sm)",
              padding: "1px 6px",
            }}
          >
            {selectedNode.type ?? "other"}
          </span>

          {/* Delete affordance — opens two-step CascadeDeleteModal (F13, ADR-0026) */}
          <button
            onClick={() => setDeleteModalOpen(true)}
            data-testid="preview-panel-delete-btn"
            aria-label={t("cascadeDelete.deleteButton")}
            title={t("cascadeDelete.deleteButton")}
            className="syn-toolbar-button"
            style={{
              marginLeft: "auto",
            }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLButtonElement).style.color = "var(--syn-red)";
              (e.currentTarget as HTMLButtonElement).style.borderColor = "color-mix(in srgb, var(--syn-red) 40%, var(--syn-border) 60%)";
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
            fontSize: 15,
            fontWeight: 600,
            color: "var(--syn-text)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
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

      {/* Scrollable body */}
      <div
        className="preview-panel__body"
        style={{
          flex: 1,
          overflow: "auto",
          padding: "12px 16px",
        }}
      >
        {/* Metadata section */}
        <section aria-labelledby="meta-heading">
          <h3
            id="meta-heading"
            style={{
              margin: "0 0 8px",
              fontSize: 11,
              fontWeight: 600,
              letterSpacing: "0.04em",
              textTransform: "uppercase",
              color: "var(--syn-text-dim)",
            }}
          >
            Metadata
          </h3>
          <dl
            style={{
              margin: 0,
              display: "grid",
              gridTemplateColumns: "auto 1fr",
              gap: "4px 12px",
              fontSize: 12,
            }}
          >
            <dt style={{ color: "var(--syn-text-muted)" }}>ID</dt>
            <dd
              style={{
                margin: 0,
                color: "var(--syn-text-dim)",
                fontFamily: "monospace",
                fontSize: 11,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
              title={selectedNode.id}
            >
              {selectedNode.id}
            </dd>
            <dt style={{ color: "var(--syn-text-muted)" }}>Degree</dt>
            <dd style={{ margin: 0, color: "var(--syn-text)" }}>{incidentEdges.length}</dd>
            <dt style={{ color: "var(--syn-text-muted)" }}>Position</dt>
            <dd style={{ margin: 0, color: "var(--syn-text-dim)", fontFamily: "monospace", fontSize: 11 }}>
              {selectedNode.x.toFixed(2)}, {selectedNode.y.toFixed(2)}
            </dd>
          </dl>
        </section>

        {/* Relationships section */}
        {incidentEdges.length > 0 && (
          <section
            aria-labelledby="rel-heading"
            style={{ marginTop: 16 }}
          >
            <h3
              id="rel-heading"
              style={{
                margin: "0 0 8px",
                fontSize: 11,
                fontWeight: 600,
                letterSpacing: "0.04em",
                textTransform: "uppercase",
                color: "var(--syn-text-dim)",
              }}
            >
              Relationships ({incidentEdges.length})
            </h3>
            <ul
              style={{
                margin: 0,
                padding: 0,
                listStyle: "none",
                display: "flex",
                flexDirection: "column",
                gap: 4,
              }}
            >
              {incidentEdges.map((edge, i) => {
                const isSource = edge.source === selectedNodeId;
                const otherId = isSource ? edge.target : edge.source;
                const other = nodeById.get(otherId);
                const otherColorVar = typeColorVar(other?.type ?? null);

                return (
                  <li
                    key={`${edge.source}-${edge.target}-${i}`}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 6,
                      fontSize: 12,
                      color: "var(--syn-text-muted)",
                    }}
                  >
                    {/* Direction arrow */}
                    <span
                      aria-hidden="true"
                      style={{ fontSize: 10, opacity: 0.6 }}
                    >
                      {isSource ? "→" : "←"}
                    </span>
                    {/* Colour dot */}
                    <span
                      aria-hidden="true"
                      style={{
                        width: 6,
                        height: 6,
                        borderRadius: "50%",
                        background: otherColorVar,
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
                      {other?.title ?? otherId}
                    </span>
                    {/* Weight chip */}
                    <span
                      aria-label={`weight ${edge.weight.toFixed(1)}`}
                      style={{
                        fontSize: 10,
                        color: "var(--syn-text-dim)",
                        background: "var(--syn-surface-sunken)",
                        border: "1px solid var(--syn-border-subtle)",
                        borderRadius: 8,
                        padding: "1px 5px",
                        flexShrink: 0,
                        fontFamily: "monospace",
                      }}
                    >
                      {edge.weight.toFixed(1)}
                    </span>
                  </li>
                );
              })}
            </ul>
          </section>
        )}

        {/* Document body placeholder — rendered content is Phase 3 scope */}
        <p
          style={{
            marginTop: 20,
            fontSize: 11,
            color: "var(--syn-border)",
            fontStyle: "italic",
            userSelect: "none",
          }}
          aria-hidden="true"
        >
          No document body — demo node (full content viewer: Phase 3)
        </p>
      </div>
    </div>
  );
}
