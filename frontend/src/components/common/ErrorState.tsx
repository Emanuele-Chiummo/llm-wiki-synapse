/**
 * ErrorState.tsx — reusable friendly error surface (audit item #1b).
 *
 * Replaces raw exception text (e.g. "Unexpected token '<'…") with:
 *   - a friendly, i18n title (default: errors.genericTitle)
 *   - an optional "Retry" button that re-runs the caller's fetch
 *   - a collapsible <details> block with the raw error message/stack
 *   - a copy-to-clipboard button inside the details block
 *
 * Visual language: CSS variables (--syn-*), same as the rest of the app.
 * Theme-aware: relies entirely on var(--syn-*) which flip in dark mode.
 *
 * I3-compliant: no Zustand, no store subscriptions, pure local state.
 */

import { useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Copy, Check, RefreshCw } from "lucide-react";

// ─── helpers ──────────────────────────────────────────────────────────────────

/** Extracts a human-readable diagnostic string from any thrown value. */
function toErrorText(error: unknown): string {
  if (error instanceof Error) {
    return error.stack ?? error.message;
  }
  if (typeof error === "string") {
    return error;
  }
  try {
    return JSON.stringify(error, null, 2);
  } catch {
    return String(error);
  }
}

// ─── component ────────────────────────────────────────────────────────────────

export interface ErrorStateProps {
  /** Friendly title shown prominently. Falls back to errors.genericTitle. */
  title?: string;
  /** When provided, a "Retry" button is rendered. Clicking it calls this. */
  onRetry?: () => void;
  /** The raw caught value. Shown in a collapsible details section. */
  error?: unknown;
}

export function ErrorState({ title, onRetry, error }: ErrorStateProps) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);

  const hasError = error !== undefined && error !== null;
  const errorText = hasError ? toErrorText(error) : "";
  const displayTitle = title ?? t("errors.genericTitle");

  const handleCopy = () => {
    if (!errorText) return;
    navigator.clipboard
      .writeText(errorText)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      })
      .catch(() => {
        /* clipboard unavailable in insecure context */
      });
  };

  return (
    <div
      data-testid="error-state"
      role="alert"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 12,
        padding: "14px 16px",
        border:
          "1px solid color-mix(in srgb, var(--syn-red, #d1242f) 30%, var(--syn-mix-base, transparent) 70%)",
        borderRadius: 8,
        background:
          "color-mix(in srgb, var(--syn-red, #d1242f) 6%, var(--syn-mix-base, transparent) 94%)",
      }}
    >
      {/* ── Title row ── */}
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <AlertTriangle
          size={16}
          aria-hidden="true"
          style={{ color: "var(--syn-red, #d1242f)", flexShrink: 0 }}
        />
        <span
          data-testid="error-state-title"
          style={{ fontWeight: 600, fontSize: 13, color: "var(--syn-red, #d1242f)" }}
        >
          {displayTitle}
        </span>
      </div>

      {/* ── Retry button (only when onRetry is provided) ── */}
      {onRetry !== undefined && (
        <button
          type="button"
          data-testid="error-state-retry"
          onClick={onRetry}
          style={{
            alignSelf: "flex-start",
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            padding: "6px 12px",
            border:
              "1px solid color-mix(in srgb, var(--syn-red, #d1242f) 30%, var(--syn-mix-base, transparent) 70%)",
            borderRadius: 6,
            background: "var(--syn-surface, #fff)",
            color: "var(--syn-red, #d1242f)",
            fontSize: 12,
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          <RefreshCw size={12} aria-hidden="true" />
          {t("common.retry")}
        </button>
      )}

      {/* ── Technical details (collapsible) ── */}
      {hasError && (
        <details data-testid="error-state-details">
          <summary
            style={{
              cursor: "pointer",
              fontSize: 11,
              color: "var(--syn-text-muted, #6e7781)",
              userSelect: "none",
              listStyle: "none",
            }}
          >
            {t("errors.technicalDetails")}
          </summary>

          <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 6 }}>
            <pre
              data-testid="error-state-detail-text"
              style={{
                margin: 0,
                padding: "8px 10px",
                background: "var(--syn-surface-sunken, #f6f8fa)",
                border: "1px solid var(--syn-border, #d0d7de)",
                borderRadius: 5,
                fontSize: 10,
                fontFamily: "monospace",
                color: "var(--syn-text-muted, #6e7781)",
                whiteSpace: "pre-wrap",
                wordBreak: "break-all",
                overflowX: "auto",
                maxHeight: 160,
                overflowY: "auto",
              }}
            >
              {errorText}
            </pre>

            {/* Copy button — mirrors SectionApiMcp copy-button pattern */}
            <button
              type="button"
              data-testid="error-state-copy"
              onClick={handleCopy}
              style={{
                alignSelf: "flex-start",
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                padding: "4px 10px",
                border: "1px solid var(--syn-border, #d0d7de)",
                borderRadius: 4,
                background: copied
                  ? "color-mix(in srgb, var(--syn-green, #1a7f37) 8%, var(--syn-mix-base, transparent) 92%)"
                  : "transparent",
                color: copied ? "var(--syn-green, #1a7f37)" : "var(--syn-text-muted, #6e7781)",
                fontSize: 11,
                cursor: "pointer",
                transition: "background 0.15s, color 0.15s",
              }}
            >
              {copied ? (
                <Check size={11} aria-hidden="true" />
              ) : (
                <Copy size={11} aria-hidden="true" />
              )}
              {t("errors.copyDetails")}
            </button>
          </div>
        </details>
      )}
    </div>
  );
}
