/**
 * DataQualityNudge.tsx — slim amber banner showing untyped and undomained page counts [F18][v1.5].
 * Uses already-fetched overview + sections data — NO new API calls on render (I3).
 * Renders null when both untyped and undomained counts are zero.
 * Extracted from HomeDashboard.tsx — behavior-preserving.
 */

import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Tag } from "lucide-react";
import { triggerBackfillDomains, triggerReclassifyTypes } from "../../api/opsClient";
import type { StatsOverview, StatsSections } from "../../api/statsClient";

interface DataQualityNudgeProps {
  overview: StatsOverview;
  sections: StatsSections | null | undefined;
}

export function DataQualityNudge({ overview, sections }: DataQualityNudgeProps) {
  const { t } = useTranslation();
  const [classifying, setClassifying] = useState(false);
  const [done, setDone] = useState(false);

  const typedCount = Object.values(overview.pages_by_type).reduce((sum, n) => sum + n, 0);
  const untypedCount = Math.max(0, overview.pages_total - typedCount);
  const undomainedCount = sections?.sections.find((s) => s.domain === "untagged")?.pages_total ?? 0;

  if (untypedCount === 0 && undomainedCount === 0) return null;

  const handleClassify = () => {
    if (classifying || done) return;
    setClassifying(true);
    void (async () => {
      try {
        await triggerBackfillDomains();
        await triggerReclassifyTypes();
        setDone(true);
      } catch {
        /* non-fatal — nudge stays visible; user can retry */
      } finally {
        setClassifying(false);
      }
    })();
  };

  return (
    <section
      aria-label={t("home.dataQuality.ariaLabel")}
      data-testid="home-data-quality"
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 12,
        padding: "8px 14px",
        borderRadius: "var(--syn-radius-md)",
        border: "1px solid color-mix(in srgb, var(--syn-amber) 25%, var(--syn-border) 75%)",
        background: "color-mix(in srgb, var(--syn-amber) 5%, var(--syn-bg-soft) 95%)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <Tag size={12} aria-hidden="true" style={{ color: "var(--syn-amber)", flexShrink: 0 }} />
        <span
          data-testid="home-data-quality-message"
          style={{ fontSize: 12, color: "var(--syn-text-muted)" }}
        >
          {t("home.dataQuality.message", {
            untyped: untypedCount,
            undomained: undomainedCount,
          })}
        </span>
      </div>
      <button
        type="button"
        data-testid="home-data-quality-cta"
        onClick={handleClassify}
        disabled={classifying || done}
        style={{
          fontSize: 11,
          padding: "4px 10px",
          borderRadius: "var(--syn-radius-md)",
          border: "1px solid var(--syn-accent)",
          background: "transparent",
          color: "var(--syn-accent)",
          cursor: classifying || done ? "default" : "pointer",
          flexShrink: 0,
          fontWeight: 500,
          opacity: classifying || done ? 0.6 : 1,
          transition: "opacity 0.1s ease",
        }}
      >
        {done
          ? t("home.dataQuality.done")
          : classifying
            ? t("home.dataQuality.running")
            : t("home.dataQuality.cta")}
      </button>
    </section>
  );
}
