/**
 * Card.tsx — shared elevated-surface primitive (W4 audit FE-QUAL-8).
 *
 * Thin wrapper over the existing .syn-card CSS class (styles/theme.css).
 */
import React from "react";

export type CardProps = React.HTMLAttributes<HTMLDivElement>;

export function Card({ className, ...rest }: CardProps) {
  const classes = ["syn-card", className ?? null].filter(Boolean).join(" ");
  return <div className={classes} {...rest} />;
}
