/**
 * SectionCard.tsx — domain section card for HomeDashboard [F18].
 * Extracted from HomeDashboard.tsx — behavior-preserving.
 */

import { useTranslation } from "react-i18next";
import { Clock } from "lucide-react";
import { TypeBar } from "./TypeBar";
import { typeColor, formatDate } from "./homeUtils";
import type { SectionEntry } from "../../api/statsClient";

interface SectionCardProps {
  section: SectionEntry;
  onNavigate: (domain: string) => void;
}

export function SectionCard({ section, onNavigate }: SectionCardProps) {
  const { t } = useTranslation();
  const isUntagged = section.domain === "untagged";
  const typeEntries = Object.entries(section.pages_by_type);

  return (
    <button
      data-testid={`section-card-${section.domain}`}
      onClick={() => onNavigate(section.domain)}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 8,
        padding: "14px 16px",
        borderRadius: "var(--syn-radius-md)",
        border: "1px solid var(--syn-border)",
        background: isUntagged ? "var(--syn-surface-sunken)" : "var(--syn-bg-soft)",
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
        (e.currentTarget as HTMLButtonElement).style.background = isUntagged
          ? "var(--syn-surface-sunken)"
          : "var(--syn-bg-soft)";
      }}
    >
      {/* Domain name + page count */}
      <div
        style={{ display: "flex", alignItems: "baseline", gap: 8, justifyContent: "space-between" }}
      >
        <span
          style={{
            fontSize: 13,
            fontWeight: 600,
            color: isUntagged ? "var(--syn-text-muted)" : "var(--syn-text)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {isUntagged ? t("home.sections.untaggedLabel") : section.domain}
        </span>
        <span
          style={{
            fontSize: 18,
            fontWeight: 700,
            color: isUntagged ? "var(--syn-text-muted)" : "var(--syn-accent)",
            flexShrink: 0,
          }}
        >
          {section.pages_total}
        </span>
      </div>

      {/* Type mini-bar */}
      {section.pages_total > 0 && (
        <TypeBar pagesByType={section.pages_by_type} total={section.pages_total} />
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

      {/* Last activity */}
      {section.last_activity && (
        <div style={{ display: "flex", alignItems: "center", gap: 4, marginTop: 2 }}>
          <Clock
            size={10}
            aria-hidden="true"
            style={{ color: "var(--syn-text-dim)", flexShrink: 0 }}
          />
          <span style={{ fontSize: 10, color: "var(--syn-text-dim)" }}>
            {formatDate(section.last_activity)}
          </span>
        </div>
      )}

      {/* Top pages */}
      {section.top_pages.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 2, marginTop: 2 }}>
          {section.top_pages.slice(0, 3).map((p) => (
            <span
              key={p.id}
              style={{
                fontSize: 10,
                color: "var(--syn-text-muted)",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {p.title}
            </span>
          ))}
        </div>
      )}

      {/* Navigate hint */}
      <span style={{ fontSize: 10, color: "var(--syn-accent)", marginTop: 2 }}>
        {t("home.sections.filterHint")} →
      </span>
    </button>
  );
}
