/**
 * GroupCard.tsx — Louvain community group card for HomeDashboard [F18].
 * Extracted from HomeDashboard.tsx — behavior-preserving.
 */

import { useTranslation } from "react-i18next";
import { Clock } from "lucide-react";
import { TypeBar } from "./TypeBar";
import { typeColor, formatDate } from "./homeUtils";
import type { StatsGroup } from "../../api/statsClient";

interface GroupCardProps {
  group: StatsGroup;
  onOpen: (group: StatsGroup) => void;
}

export function GroupCard({ group, onOpen }: GroupCardProps) {
  const { t } = useTranslation();
  const typeEntries = Object.entries(group.pages_by_type);
  const topPage = group.top_pages[0];

  return (
    <button
      data-testid={`group-card-${group.community}`}
      onClick={() => onOpen(group)}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 8,
        padding: "14px 16px",
        borderRadius: "var(--syn-radius-md)",
        border: "1px solid var(--syn-border)",
        background: "var(--syn-bg-soft)",
        boxShadow: "var(--syn-shadow-soft)",
        cursor: "pointer",
        textAlign: "left",
        transition: "border-color 0.12s ease, background 0.12s ease",
        width: "100%",
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--syn-accent)";
        (e.currentTarget as HTMLButtonElement).style.background = "var(--syn-surface-hover)";
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--syn-border)";
        (e.currentTarget as HTMLButtonElement).style.background = "var(--syn-bg-soft)";
      }}
    >
      {/* Label + page count */}
      <div
        style={{ display: "flex", alignItems: "baseline", gap: 8, justifyContent: "space-between" }}
      >
        <span
          style={{
            fontSize: 13,
            fontWeight: 600,
            color: "var(--syn-text)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {group.label}
        </span>
        <span style={{ fontSize: 16, fontWeight: 700, color: "var(--syn-accent)", flexShrink: 0 }}>
          {group.pages_total}
          <span
            style={{ fontSize: 10, fontWeight: 400, color: "var(--syn-text-dim)", marginLeft: 2 }}
          >
            {t("home.groups.pages")}
          </span>
        </span>
      </div>

      {/* Type mini-bar */}
      {group.pages_total > 0 && (
        <TypeBar pagesByType={group.pages_by_type} total={group.pages_total} />
      )}

      {/* Type breakdown text */}
      {typeEntries.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 8px" }}>
          {typeEntries.map(([type, count]) => (
            <span
              key={type}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                fontSize: 10,
                color: "var(--syn-text-dim)",
              }}
            >
              <span
                aria-hidden="true"
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: 2,
                  background: typeColor(type),
                  flexShrink: 0,
                }}
              />
              {count} {type}
            </span>
          ))}
        </div>
      )}

      {/* Top page (highest degree) */}
      {topPage ? (
        <div
          style={{
            fontSize: 10,
            color: "var(--syn-text-muted)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          <span style={{ color: "var(--syn-text-dim)" }}>{t("home.groups.topPage")}: </span>
          {topPage.title}
        </div>
      ) : (
        <div style={{ fontSize: 10, color: "var(--syn-text-dim)" }}>
          {t("home.groups.noTopPages")}
        </div>
      )}

      {/* Browse hint */}
      <span style={{ fontSize: 10, color: "var(--syn-accent)", marginTop: 2 }}>
        {t("home.groups.browseHint")} →
      </span>

      {/* Last activity */}
      {group.last_activity && (
        <div style={{ display: "flex", alignItems: "center", gap: 4, marginTop: 2 }}>
          <Clock
            size={10}
            aria-hidden="true"
            style={{ color: "var(--syn-text-dim)", flexShrink: 0 }}
          />
          <span style={{ fontSize: 10, color: "var(--syn-text-dim)" }}>
            {formatDate(group.last_activity)}
          </span>
        </div>
      )}
    </button>
  );
}
