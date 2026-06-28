/**
 * StatusBadge.tsx — color-coded status badge for ingest run cards (ADR-0018 §3).
 *
 * Colors (dark GitHub palette):
 *   running        → #1f6feb (blue) with pulsing animation
 *   completed      → #3fb950 (green)
 *   failed         → #f85149 (red)
 *   converged_false → #d29922 (amber)
 *
 * prefers-reduced-motion: pulse animation disabled (reuse pattern from GraphViewer).
 * i18n: label from ingest.status.* keys (ADR-0018 §6 / I6).
 */

import { useTranslation } from "react-i18next";
import type { IngestStatus } from "../../api/types";

const STATUS_COLOR: Record<IngestStatus, string> = {
  running:          "#1f6feb",
  completed:        "#3fb950",
  failed:           "#f85149",
  converged_false:  "#d29922",
};

const STATUS_BG: Record<IngestStatus, string> = {
  running:          "#1f6feb22",
  completed:        "#3fb95022",
  failed:           "#f8514922",
  converged_false:  "#d2992222",
};

interface StatusBadgeProps {
  status: IngestStatus;
}

export function StatusBadge({ status }: StatusBadgeProps) {
  const { t } = useTranslation();

  const labelKey = `ingest.status.${status === "converged_false" ? "convergedFalse" : status}` as const;
  const label = t(labelKey as string);
  const color = STATUS_COLOR[status] ?? "#8b949e";
  const bg = STATUS_BG[status] ?? "#8b949e22";

  const reducedMotion =
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

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
        border: `1px solid ${color}4d`,
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

      {/* Inject keyframes once — harmless if duplicated in the head */}
      <style>{`
        @keyframes synapse-pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.3; }
        }
      `}</style>
    </span>
  );
}
