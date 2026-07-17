/**
 * Skeleton.tsx — shared shimmering loading-placeholder primitive (W4 audit FE-QUAL-8).
 *
 * Thin wrapper over the existing .syn-skeleton CSS class (styles/theme.css),
 * previously duplicated as a local component in HomeDashboard.tsx. Extracted
 * so other views (Sources, Lint, Convert, Review, ...) can adopt the same
 * loading-state visual language instead of a bare "Loading…" text line.
 */
export interface SkeletonProps {
  width?: number | string;
  height: number | string;
  radius?: number;
}

export function Skeleton({ width, height, radius = 8 }: SkeletonProps) {
  return (
    <div
      className="syn-skeleton"
      aria-hidden="true"
      style={{ width: width ?? "100%", height, borderRadius: radius }}
    />
  );
}
