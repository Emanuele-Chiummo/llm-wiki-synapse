/**
 * ReviewTabButton.tsx — status tab button for the F9 Review Queue header
 * (Pending / Resolved / Dismissed). Extracted from ReviewQueueView.tsx
 * (FE-ARCH-1/FE-TEST-10 — mechanical move, no behavior change).
 */

// ─── Status tab button ────────────────────────────────────────────────────────

export interface TabButtonProps {
  label: string;
  active: boolean;
  onClick: () => void;
  testId: string;
}

export function TabButton({ label, active, onClick, testId }: TabButtonProps) {
  return (
    <button
      onClick={onClick}
      data-testid={testId}
      id={testId}
      role="tab"
      aria-selected={active}
      aria-controls="review-tabpanel"
      tabIndex={active ? 0 : -1}
      style={{
        padding: "4px 10px",
        fontSize: 11,
        fontWeight: active ? 700 : 400,
        border: active ? "1px solid var(--syn-border)" : "1px solid transparent",
        borderRadius: "var(--syn-radius-sm)",
        background: active ? "var(--syn-surface-hover)" : "transparent",
        color: active ? "var(--syn-text)" : "var(--syn-text-muted)",
        cursor: "pointer",
        whiteSpace: "nowrap",
      }}
    >
      {label}
    </button>
  );
}
