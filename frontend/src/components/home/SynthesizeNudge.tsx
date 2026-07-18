/**
 * SynthesizeNudge.tsx — banner offering corpus-level synthesis/comparison generator
 * (POST /ops/synthesize, ADR-0067 D3) [F18][v1.5.3].
 * Uses already-fetched overview data + shared synthesize status (I3).
 * Renders null when corpus has too few entity/concept pages, or a run is already in flight.
 * Extracted from HomeDashboard.tsx — behavior-preserving.
 */

import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Sparkles } from "lucide-react";
import { triggerSynthesize } from "../../api/opsClient";
import type { StatsOverview, SynthesizeStatus } from "../../api/statsClient";

/**
 * Minimum entity+concept page count before offering the trigger — mirrors the
 * backend's MIN_SYNTHESIS_CLUSTER (ops/synthesize.py).
 */
export const SYNTHESIZE_MIN_MEMBER_PAGES = 3;

/** Poll interval while a synthesize run is active (ms). */
export const SYNTHESIZE_STATUS_POLL_MS = 2_000;

interface SynthesizeNudgeProps {
  overview: StatsOverview;
  synthesizeStatus: SynthesizeStatus | null;
  /** Re-fetch status after a trigger; the parent then polls only while the run is active. */
  onTriggered: () => void;
}

export function SynthesizeNudge({ overview, synthesizeStatus, onTriggered }: SynthesizeNudgeProps) {
  const { t } = useTranslation();
  const [triggeringMode, setTriggeringMode] = useState<"auto" | "review-only" | null>(null);
  const [done, setDone] = useState(false);

  const memberPages = (overview.pages_by_type.entity ?? 0) + (overview.pages_by_type.concept ?? 0);

  if (memberPages < SYNTHESIZE_MIN_MEMBER_PAGES) return null;
  if (synthesizeStatus?.running) return null;

  const lastSummary = synthesizeStatus?.last_summary ?? null;
  const hasDiagnostics =
    lastSummary != null &&
    (lastSummary.duplicates_skipped !== undefined ||
      lastSummary.untagged_skipped !== undefined ||
      lastSummary.max_candidates !== undefined ||
      lastSummary.mode !== undefined);

  const handleTrigger = (mode: "auto" | "review-only") => {
    if (triggeringMode || done) return;
    setTriggeringMode(mode);
    void (async () => {
      try {
        await triggerSynthesize({ mode });
        setDone(true);
        onTriggered();
      } catch {
        /* non-fatal — nudge stays visible; user can retry */
      } finally {
        setTriggeringMode(null);
      }
    })();
  };

  return (
    <section
      aria-label={t("home.synthesize.ariaLabel")}
      data-testid="home-synthesize-nudge"
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 12,
        padding: "8px 14px",
        borderRadius: "var(--syn-radius-md)",
        border: "1px solid color-mix(in srgb, var(--syn-accent) 25%, var(--syn-border) 75%)",
        background: "var(--syn-bg-soft)",
      }}
    >
      <div style={{ display: "flex", alignItems: "flex-start", gap: 6, minWidth: 0 }}>
        <Sparkles
          size={12}
          aria-hidden="true"
          style={{ color: "var(--syn-accent)", flexShrink: 0 }}
        />
        <div style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0 }}>
          <span
            data-testid="home-synthesize-message"
            style={{ fontSize: 12, color: "var(--syn-text-muted)" }}
          >
            {lastSummary
              ? t("home.synthesize.messageLastRun", {
                  synthesis: lastSummary.synthesis_written,
                  comparison: lastSummary.comparison_written,
                  proposed: lastSummary.proposed,
                })
              : t("home.synthesize.message")}
          </span>
          {hasDiagnostics && lastSummary && (
            <span
              data-testid="home-synthesize-diagnostics"
              style={{ fontSize: 10, color: "var(--syn-text-dim)", overflowWrap: "anywhere" }}
            >
              {t("home.synthesize.duplicatesSkipped")}: {lastSummary.duplicates_skipped ?? 0} ·{" "}
              {t("home.synthesize.untaggedSkipped")}: {lastSummary.untagged_skipped ?? 0} ·{" "}
              {t("home.synthesize.maxCandidates")}: {lastSummary.max_candidates ?? "–"} ·{" "}
              {t("home.synthesize.mode")}: {lastSummary.mode ?? "–"}
            </span>
          )}
        </div>
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", justifyContent: "flex-end" }}>
        <button
          type="button"
          data-testid="home-synthesize-review-cta"
          onClick={() => handleTrigger("review-only")}
          disabled={triggeringMode !== null || done}
          className="syn-btn syn-btn--ghost"
          style={{ fontSize: 11, padding: "4px 10px", flexShrink: 0 }}
        >
          {triggeringMode === "review-only"
            ? t("home.synthesize.running")
            : t("home.synthesize.reviewOnly")}
        </button>
        <button
          type="button"
          data-testid="home-synthesize-cta"
          onClick={() => handleTrigger("auto")}
          disabled={triggeringMode !== null || done}
          style={{
            fontSize: 11,
            padding: "4px 10px",
            borderRadius: "var(--syn-radius-md)",
            border: "1px solid var(--syn-accent)",
            background: "transparent",
            color: "var(--syn-accent)",
            cursor: triggeringMode || done ? "default" : "pointer",
            flexShrink: 0,
            fontWeight: 500,
            opacity: triggeringMode || done ? 0.6 : 1,
            transition: "opacity 0.1s ease",
          }}
        >
          {done
            ? t("home.synthesize.done")
            : triggeringMode === "auto"
              ? t("home.synthesize.running")
              : t("home.synthesize.cta")}
        </button>
      </div>
    </section>
  );
}
