/**
 * Chip.tsx — shared rounded-pill chip primitive (W4 audit FE-QUAL-8).
 *
 * Thin wrapper over the existing .syn-chip CSS class (styles/theme.css).
 */
import React from "react";

export interface ChipProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: "default" | "accent";
}

export function Chip({ tone = "default", className, style, ...rest }: ChipProps) {
  const classes = ["syn-chip", className ?? null].filter(Boolean).join(" ");
  const toneStyle: React.CSSProperties =
    tone === "accent"
      ? { color: "var(--syn-accent)", borderColor: "var(--syn-accent)", ...style }
      : (style ?? {});
  return <span className={classes} style={toneStyle} {...rest} />;
}
