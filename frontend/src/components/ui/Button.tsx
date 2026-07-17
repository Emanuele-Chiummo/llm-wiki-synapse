/**
 * Button.tsx — shared button primitive (W4 audit FE-QUAL-8, components/ui kit).
 *
 * Thin wrapper over the existing .syn-btn CSS kit (styles/components.css).
 * Does NOT reinvent colors — every variant maps 1:1 to a .syn-btn--* modifier
 * that already exists and is theme-aware via --syn-* tokens.
 *
 * Variant → CSS class:
 *   primary      → .syn-btn--primary       (filled accent, main CTA)
 *   secondary    → .syn-btn--secondary     (bordered ghost, accent text on hover)
 *   accent-ghost → .syn-btn--accent-ghost  (soft accent fill — replaces the old
 *                                            settings/ui.tsx BTN_PRIMARY constant)
 *   ghost        → .syn-btn--ghost         (borderless — replaces BTN_SECONDARY)
 *   danger       → .syn-btn--danger        (red outline, destructive non-modal)
 */
import React from "react";

export type ButtonVariant = "primary" | "secondary" | "accent-ghost" | "ghost" | "danger";
export type ButtonSize = "md" | "sm";

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", className, type = "button", ...rest },
  ref,
) {
  const classes = [
    "syn-btn",
    `syn-btn--${variant}`,
    size === "sm" ? "syn-btn--sm" : null,
    className ?? null,
  ]
    .filter(Boolean)
    .join(" ");

  return <button ref={ref} type={type} className={classes} {...rest} />;
});
