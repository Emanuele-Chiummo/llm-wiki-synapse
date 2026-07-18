/**
 * TypeBar.tsx — inline type-composition bar and sparkline for HomeDashboard [F18].
 * Extracted from HomeDashboard.tsx (pure visual, no API calls, no store subscriptions).
 */

import { typeColor } from "./homeUtils";

// ─── TypeBar ──────────────────────────────────────────────────────────────────

interface TypeBarProps {
  pagesByType: Record<string, number>;
  total: number;
}

/**
 * HTML flex bar (not SVG) so segments carry a real 2px surface gap and their
 * own rounded ends. Colour resolves from CSS tokens directly.
 */
export function TypeBar({ pagesByType, total }: TypeBarProps) {
  if (total === 0) return null;
  const entries = Object.entries(pagesByType).filter(([, count]) => count > 0);
  return (
    <div aria-hidden="true" style={{ display: "flex", gap: 2, height: 6, width: "100%" }}>
      {entries.map(([type, count]) => (
        <div
          key={type}
          style={{
            flexGrow: count,
            flexBasis: 0,
            minWidth: 2,
            background: typeColor(type),
            borderRadius: 2,
          }}
        />
      ))}
    </div>
  );
}

// ─── Sparkline ────────────────────────────────────────────────────────────────

/**
 * Tiny inline trend line for a KPI (e.g. daily cost over the last 30 days).
 * Stretched to the card width; non-scaling stroke keeps the line crisp.
 */
export function Sparkline({
  values,
  color = "var(--syn-accent)",
}: {
  values: number[];
  color?: string;
}) {
  if (values.length < 2) return null;
  const W = 100;
  const H = 22;
  const PAD = 1.5;
  const max = Math.max(...values);
  const min = Math.min(...values);
  const range = max - min || 1;
  const stepX = (W - PAD * 2) / (values.length - 1);
  const pts = values.map((v, i) => {
    const x = PAD + i * stepX;
    const y = PAD + (H - PAD * 2) * (1 - (v - min) / range);
    return [x, y] as const;
  });
  const line = pts
    .map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`)
    .join(" ");
  const last = pts[pts.length - 1] ?? [0, 0];
  const first = pts[0] ?? [0, 0];
  const area = `${line} L${last[0].toFixed(1)} ${H} L${first[0].toFixed(1)} ${H} Z`;
  return (
    <svg
      width="100%"
      height={H}
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      aria-hidden="true"
      style={{ display: "block", overflow: "visible" }}
    >
      <path d={area} fill={color} opacity={0.1} />
      <path
        d={line}
        fill="none"
        stroke={color}
        strokeWidth={1.5}
        vectorEffect="non-scaling-stroke"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      <circle cx={last[0]} cy={last[1]} r={2} fill={color} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
