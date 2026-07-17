/**
 * StatusBar.tsx — bottom-center graph stats/status overlay (nodes/edges/version/cache).
 * Move-only extraction from GraphViewer.tsx (FE-ARCH-1, 1.9.3 W2) — no behavior change.
 */

import React from "react";
import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  selectEdges,
  selectNodes,
  useGraphMeta,
  useGraphStatus,
  useGraphStore,
} from "../../store/graphStore";
import { reducedMotion } from "./graphViewerShared";

// ─── Status bar ───────────────────────────────────────────────────────────────

export const StatusBar: React.FC<{ isRefetching?: boolean }> = ({ isRefetching = false }) => {
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
      aria-label={t("graph.statsAriaLabel")}
    >
      <span>{nodes.length} nodes</span>
      <span>{edges.length} edges</span>
      {dataVersion !== null && <span>v{dataVersion}</span>}
      {/* UX-1: "updating…" pill — visible only while a version-bump re-fetch is in-flight */}
      {isRefetching && (
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 3,
            fontSize: 10,
            fontWeight: 600,
            color: "var(--syn-accent)",
            background: "var(--syn-accent-soft)",
            borderRadius: "var(--syn-radius-pill)",
            padding: "1px 6px",
          }}
        >
          <Loader2
            size={9}
            aria-hidden="true"
            style={{
              animation: reducedMotion ? "none" : "syn-spin 0.8s linear infinite",
              flexShrink: 0,
            }}
          />
          {t("home.updating")}
        </span>
      )}
      {cacheStatus !== "unknown" && (
        <span style={{ color: cacheStatus === "hit" ? "var(--syn-green)" : "var(--syn-amber)" }}>
          {cacheStatus}
        </span>
      )}
    </div>
  );
};
