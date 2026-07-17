/**
 * NodeTooltip.tsx — sigma graph node hover tooltip.
 * Move-only extraction from GraphViewer.tsx (FE-ARCH-1, 1.9.3 W2) — no behavior change.
 */

import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { fetchPageDetail } from "../../api/graphClient";
import type { PageDetail } from "../../api/types";
import { colorForType } from "./graphViewerShared";

interface TooltipProps {
  nodeId: string;
  position: { x: number; y: number };
  neighborCount: number;
  onClose: () => void;
}

export const NodeTooltip: React.FC<TooltipProps> = ({
  nodeId,
  position,
  neighborCount,
  onClose,
}) => {
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
