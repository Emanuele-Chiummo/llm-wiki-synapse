/**
 * GraphZoomControls.tsx — Zoom-in / Zoom-out / Fit camera control cluster.
 *
 * Move-only extraction from GraphViewer.tsx (FE-ARCH-1, 2.1 pass-2).
 * No behavior change. Positioned top-right of the canvas area (absolute overlay).
 *
 * INVARIANT I2: handlers only manipulate the sigma CAMERA — no layout algorithm,
 *   no rAF physics, no coordinate mutation.
 */

import React from "react";
import { ZoomIn, ZoomOut, Maximize2 } from "lucide-react";
import { useTranslation } from "react-i18next";

interface GraphZoomControlsProps {
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFit: () => void;
}

const BUTTON_STYLE: React.CSSProperties = {
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
};

export const GraphZoomControls: React.FC<GraphZoomControlsProps> = ({
  onZoomIn,
  onZoomOut,
  onFit,
}) => {
  const { t } = useTranslation();

  return (
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
        onClick={onZoomIn}
        data-testid="graph-zoom-in"
        aria-label={t("graph.zoomIn")}
        title={t("graph.zoomIn")}
        style={BUTTON_STYLE}
      >
        <ZoomIn size={14} strokeWidth={1.8} aria-hidden="true" />
      </button>
      <button
        type="button"
        onClick={onZoomOut}
        data-testid="graph-zoom-out"
        aria-label={t("graph.zoomOut")}
        title={t("graph.zoomOut")}
        style={BUTTON_STYLE}
      >
        <ZoomOut size={14} strokeWidth={1.8} aria-hidden="true" />
      </button>
      <button
        type="button"
        onClick={onFit}
        data-testid="graph-fit"
        aria-label={t("graph.fit")}
        title={t("graph.fit")}
        style={BUTTON_STYLE}
      >
        <Maximize2 size={14} strokeWidth={1.8} aria-hidden="true" />
      </button>
    </div>
  );
};
