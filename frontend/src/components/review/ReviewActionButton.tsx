/**
 * ReviewActionButton.tsx — per-item action button for the F9 Review Queue.
 * Extracted from ReviewQueueView.tsx (FE-ARCH-1/FE-TEST-10 — mechanical
 * move, no behavior change).
 */

// ─── Action button ────────────────────────────────────────────────────────────

export interface ActionButtonProps {
  label: string;
  onClick: () => void;
  disabled: boolean;
  loading?: boolean;
  variant: "create" | "approve" | "skip" | "dismiss" | "deep-research";
  /**
   * Visual weight. "primary" renders the filled accent CTA (llm_wiki review parity:
   * the leading Deep-Research action is the dark/filled primary button — Synapse
   * substitutes the brand accent per the never-black policy). Default "secondary"
   * keeps the ghost/outline appearance for Create / Approve / Skip.
   */
  emphasis?: "primary" | "secondary";
}

/**
 * ActionButton — review queue per-item action (Create / Skip / Dismiss / Deep-Research).
 * UXB-2 AC-UXB2-2: uses .syn-btn .syn-btn--secondary .syn-btn--sm as base.
 * Variant-specific color overrides are applied via inline style only for the color/border
 * (appearance tokens only — layout stays in the class).
 */
export function ActionButton({
  label,
  onClick,
  disabled,
  loading,
  variant,
  emphasis = "secondary",
}: ActionButtonProps) {
  // Map variant → token-safe inline color overrides (only when enabled).
  // These narrow the secondary ghost base to the variant's semantic color.
  const VARIANT_STYLE: Record<string, { color: string; borderColor: string }> = {
    create: {
      color: "var(--syn-green)",
      borderColor: "color-mix(in srgb, var(--syn-green) 30%, var(--syn-border) 70%)",
    },
    approve: {
      color: "var(--syn-accent)",
      borderColor: "color-mix(in srgb, var(--syn-accent) 30%, var(--syn-border) 70%)",
    },
    skip: { color: "var(--syn-text-muted)", borderColor: "var(--syn-border)" },
    dismiss: {
      color: "var(--syn-red)",
      borderColor: "color-mix(in srgb, var(--syn-red) 30%, var(--syn-border) 70%)",
    },
    "deep-research": {
      color: "var(--syn-accent)",
      borderColor: "color-mix(in srgb, var(--syn-accent) 30%, var(--syn-border) 70%)",
    },
  };
  const fallbackStyle = { color: "var(--syn-text-muted)", borderColor: "var(--syn-border)" };
  const variantStyle = VARIANT_STYLE[variant] ?? fallbackStyle;
  const isDisabled = disabled || loading;
  const isPrimary = emphasis === "primary";
  return (
    <button
      onClick={onClick}
      disabled={isDisabled}
      aria-label={label}
      aria-busy={loading}
      data-testid={`review-action-${variant}`}
      // Primary → filled accent CTA (never-black brand); secondary → ghost/outline.
      className={`syn-btn ${isPrimary ? "syn-btn--primary" : "syn-btn--secondary"} syn-btn--sm`}
      // The filled primary already carries its own accent bg + white text — no per-variant
      // color override (that would fight the fill). Only ghost/secondary buttons get tinted.
      style={
        isDisabled || isPrimary
          ? undefined
          : { color: variantStyle.color, borderColor: variantStyle.borderColor }
      }
    >
      {loading && (
        <span
          aria-hidden="true"
          style={{
            display: "inline-block",
            width: 8,
            height: 8,
            borderRadius: "50%",
            border: "1.5px solid currentColor",
            borderTopColor: "transparent",
            animation: "syn-spin 0.7s linear infinite",
          }}
        />
      )}
      {label}
    </button>
  );
}
