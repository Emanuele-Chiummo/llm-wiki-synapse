/**
 * KpiSection.tsx — KpiCard and CompositionHero for HomeDashboard [F18].
 * Extracted from HomeDashboard.tsx — behavior-preserving, no API calls.
 */

import { useTranslation } from "react-i18next";
import { TypeBar, Sparkline } from "./TypeBar";
import { typeColor } from "./homeUtils";

// ─── KpiCard ──────────────────────────────────────────────────────────────────

interface KpiCardProps {
  icon: import("react").ReactNode;
  label: string;
  value: string | number;
  accent?: boolean;
  testId?: string;
  /** When set the card becomes a real navigation control (button + hover + focus ring). */
  onClick?: () => void;
  /** Optional trend series rendered as a sparkline under the value. */
  sparkline?: number[] | undefined;
  /**
   * Semantic status of the metric: "good" paints the value green,
   * "warn" paints it amber. Omitted = neutral / accent-driven.
   */
  tone?: "good" | "warn" | undefined;
}

export function KpiCard({
  icon,
  label,
  value,
  accent,
  testId,
  onClick,
  sparkline,
  tone,
}: KpiCardProps) {
  const tileClass = `syn-stat-tile${accent ? " syn-stat-tile--accent" : ""}`;
  const toneColor =
    tone === "good" ? "var(--syn-success)" : tone === "warn" ? "var(--syn-warn)" : undefined;
  const iconColor = toneColor ?? (accent ? "var(--syn-accent)" : "var(--syn-text-dim)");

  const body = (
    <>
      <div className="syn-stat-tile__label">
        <span style={{ color: iconColor, flexShrink: 0 }}>{icon}</span>
        <span>{label}</span>
      </div>
      <span className="syn-stat-tile__value" style={toneColor ? { color: toneColor } : undefined}>
        {value}
      </span>
      {sparkline && sparkline.length >= 2 && (
        <div style={{ marginTop: 4 }}>
          <Sparkline values={sparkline} />
        </div>
      )}
    </>
  );

  if (!onClick) {
    return (
      <div data-testid={testId ?? `kpi-${label}`} className={tileClass}>
        {body}
      </div>
    );
  }

  return (
    <button
      type="button"
      data-testid={testId ?? `kpi-${label}`}
      onClick={onClick}
      aria-label={`${label}: ${value}`}
      className={tileClass}
      style={{ cursor: "pointer", textAlign: "left", transition: "border-color 0.12s ease" }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--syn-accent)";
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLButtonElement).style.borderColor = accent
          ? "color-mix(in srgb, var(--syn-accent) 30%, var(--syn-border) 70%)"
          : "var(--syn-border)";
      }}
    >
      {body}
    </button>
  );
}

// ─── CompositionHero ──────────────────────────────────────────────────────────

interface CompositionHeroProps {
  pagesTotal: number;
  pagesByType: Record<string, number>;
  onClick?: () => void;
}

/**
 * The dashboard's visual anchor: the total page count set large, above a full-width
 * per-type composition bar (jewel tones) and an inline legend.
 */
export function CompositionHero({ pagesTotal, pagesByType, onClick }: CompositionHeroProps) {
  const { t } = useTranslation();
  const entries = Object.entries(pagesByType)
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1]);

  const inner = (
    <>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 10 }}>
        <span
          style={{
            fontSize: 30,
            fontWeight: 650,
            lineHeight: 1,
            color: "var(--syn-text)",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {pagesTotal}
        </span>
        <span
          className="syn-eyebrow"
          style={{ color: "var(--syn-text-muted)", letterSpacing: "0.06em" }}
        >
          {t("home.kpi.pagesTotal")}
        </span>
      </div>
      {pagesTotal > 0 && <TypeBar pagesByType={pagesByType} total={pagesTotal} />}
      {entries.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 14px", marginTop: 10 }}>
          {entries.map(([type, n]) => (
            <span
              key={type}
              style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11 }}
            >
              <span
                aria-hidden="true"
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  background: typeColor(type),
                  flexShrink: 0,
                }}
              />
              <span
                style={{
                  color: "var(--syn-text)",
                  fontVariantNumeric: "tabular-nums",
                  fontWeight: 550,
                }}
              >
                {n}
              </span>
              <span style={{ color: "var(--syn-text-dim)", textTransform: "capitalize" }}>
                {type}
              </span>
            </span>
          ))}
        </div>
      )}
    </>
  );

  const shared: import("react").CSSProperties = {
    padding: "16px 18px",
    borderRadius: "var(--syn-radius-md)",
    border: "1px solid var(--syn-border)",
    background: "var(--syn-bg-soft)",
    boxShadow: "var(--syn-shadow-soft)",
    width: "100%",
  };

  if (!onClick) {
    return (
      <div data-testid="home-composition-hero" style={shared}>
        {inner}
      </div>
    );
  }
  return (
    <button
      type="button"
      data-testid="home-composition-hero"
      onClick={onClick}
      aria-label={t("home.kpi.pagesTotal")}
      style={{
        ...shared,
        textAlign: "left",
        cursor: "pointer",
        transition: "border-color 0.12s ease",
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--syn-accent)";
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--syn-border)";
      }}
    >
      {inner}
    </button>
  );
}
