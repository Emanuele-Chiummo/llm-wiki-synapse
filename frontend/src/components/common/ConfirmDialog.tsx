/**
 * ConfirmDialog.tsx — shared accessible confirmation dialog (R7-12 / R7-4).
 *
 * Renders a modal overlay with role="alertdialog", focus-trap (basic: Tab stays
 * inside the two buttons), and Esc = cancel.
 *
 * Props:
 *   title        — dialog heading (required)
 *   body         — explanatory text (required)
 *   confirmLabel — primary action label
 *   cancelLabel  — secondary action label
 *   danger       — when true, styles the confirm button with --syn-red
 *   onConfirm()  — called when user confirms
 *   onCancel()   — called when user cancels or presses Esc
 *
 * This component does NOT use window.confirm() (AC-R7-12-2).
 *
 * Accessibility:
 *   - role="alertdialog" aria-modal="true" aria-labelledby / aria-describedby
 *   - Focus is moved to the Cancel button on mount (safe default)
 *   - Tab and Shift+Tab cycle only between the two buttons
 *   - Escape closes with onCancel
 */

import { useEffect, useRef, type ReactNode, type MouseEvent } from "react";

export interface ConfirmDialogProps {
  title: string;
  body: string;
  confirmLabel: string;
  cancelLabel: string;
  /** When true, confirm button uses --syn-red background (destructive action). */
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  title,
  body,
  confirmLabel,
  cancelLabel,
  danger = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps): ReactNode {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const titleId = "confirm-dialog-title";
  const bodyId = "confirm-dialog-body";

  // Move focus to Cancel on mount (safe default — avoids accidental confirm).
  useEffect(() => {
    cancelRef.current?.focus();
  }, []);

  // Esc key closes with onCancel.
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        onCancel();
      }
      // Basic focus trap: Tab cycles between cancel and confirm only.
      if (e.key === "Tab") {
        const buttons = [cancelRef.current, confirmRef.current].filter(
          (b): b is HTMLButtonElement => b !== null,
        );
        if (buttons.length < 2) return;
        // length checked above (>=2), so indexing is safe
        const first = buttons[0] as HTMLButtonElement;
        const last = buttons[buttons.length - 1] as HTMLButtonElement;
        const focused = document.activeElement;
        if (e.shiftKey) {
          if (focused === first) {
            e.preventDefault();
            last.focus();
          }
        } else {
          if (focused === last) {
            e.preventDefault();
            first.focus();
          }
        }
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onCancel]);

  // Click on the overlay backdrop closes with onCancel.
  function handleBackdropClick(e: MouseEvent<HTMLDivElement>) {
    if (e.target === e.currentTarget) onCancel();
  }

  return (
    <div
      data-testid="confirm-dialog-overlay"
      onClick={handleBackdropClick}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1000,
        background: "rgba(0, 0, 0, 0.55)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={bodyId}
        data-testid="confirm-dialog"
        style={{
          background: "var(--syn-bg-card, var(--syn-bg-soft))",
          border: "1px solid var(--syn-border)",
          borderRadius: 8,
          boxShadow: "0 8px 32px rgba(0,0,0,0.35)",
          padding: "20px 24px",
          width: "min(420px, calc(100vw - 32px))",
          display: "flex",
          flexDirection: "column",
          gap: 12,
        }}
      >
        <h2
          id={titleId}
          style={{
            margin: 0,
            fontSize: 15,
            fontWeight: 700,
            color: "var(--syn-text)",
          }}
        >
          {title}
        </h2>

        <p
          id={bodyId}
          style={{
            margin: 0,
            fontSize: 13,
            color: "var(--syn-text-muted)",
            lineHeight: 1.5,
          }}
        >
          {body}
        </p>

        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
            gap: 8,
            marginTop: 4,
          }}
        >
          <button
            ref={cancelRef}
            type="button"
            data-testid="confirm-dialog-cancel"
            onClick={onCancel}
            className="syn-button syn-button--secondary"
          >
            {cancelLabel}
          </button>

          <button
            ref={confirmRef}
            type="button"
            data-testid="confirm-dialog-confirm"
            onClick={onConfirm}
            style={
              danger
                ? {
                    background: "var(--syn-red)",
                    color: "#fff",
                    border: "1px solid var(--syn-red)",
                    borderRadius: "var(--syn-radius-sm, 4px)",
                    padding: "5px 14px",
                    cursor: "pointer",
                    fontSize: 13,
                    fontWeight: 600,
                  }
                : undefined
            }
            className={danger ? undefined : "syn-button syn-button--primary"}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
