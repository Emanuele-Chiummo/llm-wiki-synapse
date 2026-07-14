/**
 * StatusBadge.tsx — color-coded status badge for ingest run cards (ADR-0018 §3).
 *
 * Colors mapped to --syn-* design tokens (light + dark compliant):
 *   running        → --syn-accent (blue) with pulsing animation
 *   completed      → --syn-green
 *   failed         → --syn-red
 *   converged_false → --syn-amber
 *   cancelling     → --syn-text-dim (muted gray — optimistic transient state, R13-3)
 *   cancelled      → --syn-text-dim (muted gray — terminal, R13-3)
 *
 * prefers-reduced-motion: pulse animation disabled (reuse pattern from GraphViewer).
 * i18n: label from ingest.status.* keys (ADR-0018 §6 / I6).
 */

import { useTranslation } from "react-i18next";
import type { IngestStatus } from "../../api/types";

const STATUS_COLOR: Record<IngestStatus, string> = {
  running: "var(--syn-accent)",
  completed: "var(--syn-green)",
  failed: "var(--syn-red)",
  converged_false: "var(--syn-amber)",
  cancelling: "var(--syn-text-dim)",
  cancelled: "var(--syn-text-dim)",
};

const STATUS_BG: Record<IngestStatus, string> = {
  running: "color-mix(in srgb, var(--syn-accent) 10%, var(--syn-mix-base) 90%)",
  completed: "color-mix(in srgb, var(--syn-green) 10%, var(--syn-mix-base) 90%)",
  failed: "color-mix(in srgb, var(--syn-red) 10%, var(--syn-mix-base) 90%)",
  converged_false: "color-mix(in srgb, var(--syn-amber) 10%, var(--syn-mix-base) 90%)",
  cancelling: "color-mix(in srgb, var(--syn-text-dim) 10%, var(--syn-mix-base) 90%)",
  cancelled: "color-mix(in srgb, var(--syn-text-dim) 10%, var(--syn-mix-base) 90%)",
};

const STATUS_BORDER: Record<IngestStatus, string> = {
  running: "1px solid color-mix(in srgb, var(--syn-accent) 30%, transparent)",
  completed: "1px solid color-mix(in srgb, var(--syn-green) 30%, transparent)",
  failed: "1px solid color-mix(in srgb, var(--syn-red) 30%, transparent)",
  converged_false: "1px solid color-mix(in srgb, var(--syn-amber) 30%, transparent)",
  cancelling: "1px solid color-mix(in srgb, var(--syn-text-dim) 30%, transparent)",
  cancelled: "1px solid color-mix(in srgb, var(--syn-text-dim) 30%, transparent)",
};

interface StatusBadgeProps {
  status: IngestStatus;
}

export function StatusBadge({ status }: StatusBadgeProps) {
  const { t } = useTranslation();

  const labelKey =
    `ingest.status.${status === "converged_false" ? "convergedFalse" : status}` as string;
  const label = t(labelKey as string);
  const color = STATUS_COLOR[status] ?? "var(--syn-text-dim)";
  const bg =
    STATUS_BG[status] ?? "color-mix(in srgb, var(--syn-text-dim) 10%, var(--syn-mix-base) 90%)";
  const border =
    STATUS_BORDER[status] ?? "1px solid color-mix(in srgb, var(--syn-text-dim) 30%, transparent)";

  const reducedMotion =
    typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  return (
    <span
      aria-label={label}
      data-status={status}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 5,
        fontSize: 11,
        fontWeight: 600,
        color,
        background: bg,
        border,
        borderRadius: 10,
        padding: "2px 7px",
        whiteSpace: "nowrap",
        userSelect: "none",
      }}
    >
      {/* Dot indicator */}
      <span
        aria-hidden="true"
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: color,
          flexShrink: 0,
          // Pulse animation only for "running", disabled if reduced-motion
          animation:
            status === "running" && !reducedMotion
              ? "synapse-pulse 1.4s ease-in-out infinite"
              : "none",
        }}
      />
      {label}

      {/* UXA-28: @keyframes synapse-pulse is declared globally in theme.css — no inline <style> needed */}
    </span>
  );
}
