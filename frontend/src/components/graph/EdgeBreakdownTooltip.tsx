/**
 * EdgeBreakdownTooltip.tsx — (R9-5) popover showing 4-signal edge weight breakdown.
 * Fetched on click (edge click in sigma, 150ms debounce on approach not needed for click).
 * Cached per (src, tgt) pair in parent component state.
 * Move-only extraction from GraphViewer.tsx (FE-ARCH-1, 1.9.3 W2) — no behavior change.
 */

import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { fetchEdgeDetail, ApiError } from "../../api/graphClient";
import type { EdgeDetail } from "../../api/graphClient";

interface EdgeBreakdownTooltipProps {
  srcId: string;
  tgtId: string;
  position: { x: number; y: number };
  cache: Map<string, EdgeDetail>;
  onCached: (key: string, detail: EdgeDetail) => void;
  onClose: () => void;
}

export const EdgeBreakdownTooltip: React.FC<EdgeBreakdownTooltipProps> = ({
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
        style={{
          color: "var(--syn-text)",
          fontFamily: "var(--syn-font-mono)",
          fontWeight: bold ? 700 : 400,
        }}
      >
        {value.toFixed(3)}
      </span>
    </div>
  );
}
