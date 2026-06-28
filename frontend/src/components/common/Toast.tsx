/**
 * Toast.tsx — minimal transient notification (success / error) for Run-Ingest + provider change.
 *
 * Usage: call the exported `useToast()` hook; render <ToastHost/> once in AppShell.
 * Toast auto-dismisses after 4s. Reduced-motion: instant show/hide (no animation).
 */

import { useState, useCallback, useRef, useEffect, type Dispatch, type SetStateAction } from "react";

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
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    timerRef.current = setTimeout(() => onDismiss(toast.id), 4000);
    return () => { if (timerRef.current) clearTimeout(timerRef.current); };
  }, [toast.id, onDismiss]);

  const color = toast.variant === "error" ? "#f85149" : "#3fb950";
  const bg = toast.variant === "error" ? "#1a0f0f" : "#0d1a10";

  return (
    <div
      role="status"
      style={{
        pointerEvents: "auto",
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "8px 12px",
        background: bg,
        border: `1px solid ${color}4d`,
        borderRadius: 6,
        fontSize: 13,
        color: "#e6edf3",
        minWidth: 240,
        maxWidth: 380,
        boxShadow: "0 4px 12px rgba(0,0,0,0.4)",
      }}
    >
      <span style={{ color, fontSize: 14, flexShrink: 0 }}>
        {toast.variant === "error" ? "✕" : "✓"}
      </span>
      <span style={{ flex: 1 }}>{toast.message}</span>
      <button
        onClick={() => onDismiss(toast.id)}
        style={{
          background: "none",
          border: "none",
          color: "#484f58",
          cursor: "pointer",
          fontSize: 12,
          padding: 0,
          flexShrink: 0,
        }}
        aria-label="Close notification"
      >
        ✕
      </button>
    </div>
  );
}
