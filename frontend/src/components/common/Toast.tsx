/**
 * Toast.tsx — minimal transient notification (success / error) for Run-Ingest + provider change.
 *
 * Usage: call the exported `useToast()` hook; render <ToastHost/> once in AppShell.
 * Toast auto-dismisses after 4s. Reduced-motion: instant show/hide (no animation).
 */

import {
  useState,
  useCallback,
  useRef,
  useEffect,
  type Dispatch,
  type SetStateAction,
} from "react";
import { useTranslation } from "react-i18next";

export type ToastVariant = "success" | "error";

export interface ToastMessage {
  id: number;
  message: string;
  variant: ToastVariant;
}

// ─── Singleton state ──────────────────────────────────────────────────────────

let toastIdCounter = 0;
let globalSetToasts: Dispatch<SetStateAction<ToastMessage[]>> | null = null;

export function showToast(message: string, variant: ToastVariant = "success"): void {
  if (!globalSetToasts) return;
  const id = ++toastIdCounter;
  globalSetToasts((prev) => [...prev, { id, message, variant }]);
}

// ─── ToastHost — render once in AppShell ─────────────────────────────────────

export function ToastHost() {
  const [toasts, setToasts] = useState<ToastMessage[]>([]);

  // Register the setter so showToast() can reach it
  useEffect(() => {
    globalSetToasts = setToasts;
    return () => { globalSetToasts = null; };
  }, []);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  return (
    <div
      aria-live="polite"
      aria-atomic="false"
      style={{
        position: "fixed",
        bottom: 40,
        right: 16,
        zIndex: 9999,
        display: "flex",
        flexDirection: "column",
        gap: 8,
        pointerEvents: "none",
      }}
    >
      {toasts.map((toast) => (
        <ToastItem key={toast.id} toast={toast} onDismiss={dismiss} />
      ))}
    </div>
  );
}

// ─── ToastItem ─────────────────────────────────────────────────────────────────

function ToastItem({ toast, onDismiss }: { toast: ToastMessage; onDismiss: (id: number) => void }) {
  const { t } = useTranslation();
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Start the auto-dismiss countdown.
  const startTimer = useCallback(() => {
    timerRef.current = setTimeout(() => onDismiss(toast.id), 4000);
  }, [toast.id, onDismiss]);

  // Clear the countdown without dismissing (pause on hover/focus).
  const clearTimer = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => {
    startTimer();
    return clearTimer;
  }, [startTimer, clearTimer]);

  const isError = toast.variant === "error";

  // UXA-16: error toasts use role="alert" (assertive live region) so screen readers
  // announce them immediately. Informational toasts use role="status" (polite).
  // a11y-toast (WCAG 2.2.1): hover or focus pauses the auto-dismiss timer;
  // leaving or blurring restarts it so keyboard/pointer users can read the message.
  return (
    <div
      role={isError ? "alert" : "status"}
      className={`syn-section-notice syn-section-notice--${isError ? "danger" : "success"}`}
      onMouseEnter={clearTimer}
      onMouseLeave={startTimer}
      onFocus={clearTimer}
      onBlur={startTimer}
      style={{
        pointerEvents: "auto",
        display: "flex",
        alignItems: "center",
        gap: 10,
        minWidth: 240,
        maxWidth: 380,
        boxShadow: "var(--syn-shadow-pop)",
      }}
    >
      <span style={{ fontSize: 14, flexShrink: 0 }}>{isError ? "✕" : "✓"}</span>
      <span style={{ flex: 1 }}>{toast.message}</span>
      <button
        onClick={() => onDismiss(toast.id)}
        style={{
          background: "none",
          border: "none",
          color: "inherit",
          opacity: 0.7,
          cursor: "pointer",
          fontSize: 12,
          padding: 0,
          flexShrink: 0,
        }}
        aria-label={t("common.close")}
      >
        ✕
      </button>
    </div>
  );
}
