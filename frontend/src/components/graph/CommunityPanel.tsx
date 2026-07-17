/**
 * CommunityPanel.tsx — (R9-5) side panel that opens when a community legend entry
 * is clicked. Fetches GET /graph/communities/{id} on demand (I2: read-only, no layout).
 * Move-only extraction from GraphViewer.tsx (FE-ARCH-1, 1.9.3 W2) — no behavior change.
 */

import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { fetchCommunityDetail, ApiError } from "../../api/graphClient";
import type { CommunityDetail } from "../../api/graphClient";

interface CommunityPanelProps {
  communityId: number;
  communityColor: string;
  onClose: () => void;
  onNavigate: (pageId: string) => void;
}

export const CommunityPanel: React.FC<CommunityPanelProps> = ({
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
                background: "color-mix(in srgb, var(--syn-amber) 10%, var(--syn-mix-base) 90%)",
                border: "1px solid color-mix(in srgb, var(--syn-amber) 30%, transparent 70%)",
                borderRadius: 4,
                fontSize: 11,
                color: "var(--syn-warn-text)",
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
