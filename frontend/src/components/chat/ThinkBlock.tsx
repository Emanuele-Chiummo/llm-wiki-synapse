/**
 * ThinkBlock.tsx — collapsible reasoning block (F7 / ADR-0019 §2.4 / R7-9).
 *
 * Used in TWO modes:
 *   1. Streaming: inside <StreamingMessage>, receives live `content` from the streaming store.
 *   2. Settled: re-derived from the stored raw assistant message (pure string split).
 *
 * Collapsed by default (AC-F7-1).
 * Content is the raw think text (plain, no markdown parse — I3).
 * No regex-per-token on the client — the server already split think vs. token events.
 *
 * R7-9 — Streaming preview (AC-R7-9-1 / AC-R7-9-2 / AC-R7-9-3):
 *   When `streaming=true` AND the block is collapsed, we show the last 3 lines of the
 *   think buffer in a muted/faded strip below the header. This is a simple string slice
 *   on the already-buffered `content` prop — NOT a per-token parse (I3-safe).
 *   The preview disappears when </think> arrives (streaming=false, settled behaviour).
 *   Gated by VITE_SHOW_THINKING env flag (same gate as the full block).
 *   Respects prefers-reduced-motion: no fade animation, static last line only.
 */

import { useState, useMemo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

// ─── prefers-reduced-motion detection (module level, stable reference) ────────

const reducedMotion: boolean =
  typeof window !== "undefined" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// ─── VITE_SHOW_THINKING gate ──────────────────────────────────────────────────

const SHOW_THINKING = import.meta.env["VITE_SHOW_THINKING"] !== "false";

interface ThinkBlockProps {
  /** Raw reasoning text (no parse needed — displayed as pre-wrap). */
  content: string;
  /** True while the think stream is still live (shows a cursor + rolling preview). */
  streaming?: boolean;
}

/**
 * Extract the last N non-empty lines from a string.
 * Pure string manipulation — no markdown parse (I3-safe).
 */
function lastLines(text: string, n: number): string {
  const lines = text.split("\n").filter((l) => l.trim().length > 0);
  return lines.slice(-n).join("\n");
}

export function ThinkBlock({ content, streaming = false }: ThinkBlockProps): ReactNode {
  const [open, setOpen] = useState(false);
  const { t } = useTranslation();

  // Env gate: when VITE_SHOW_THINKING is absent or "false", never render.
  if (!SHOW_THINKING) return null;
  if (!content) return null;

  // R7-9: rolling preview — last 3 lines of the think buffer (collapsed + streaming only).
  // Computed from the buffered prop string: no per-token overhead.
  // useMemo key: content string (changes on each chunk boundary, not per-token).
  // eslint-disable-next-line react-hooks/rules-of-hooks
  const previewText = useMemo(
    () => (streaming && !open ? lastLines(content, reducedMotion ? 1 : 3) : ""),
    [content, streaming, open],
  );

  const showPreview = streaming && !open && previewText.length > 0;

  return (
    <div
      style={{
        margin: "0 0 8px 0",
        border: "1px solid var(--syn-border)",
        borderRadius: 6,
        overflow: "hidden",
        fontSize: 12,
      }}
    >
      {/* ── Toggle header ────────────────────────────────────────────────────── */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        style={{
          width: "100%",
          display: "flex",
          alignItems: "center",
          gap: 6,
          padding: "4px 10px",
          background: "var(--syn-surface-sunken)",
          border: "none",
          borderBottom: open || showPreview ? "1px solid var(--syn-border)" : "none",
          color: "var(--syn-text-muted)",
          cursor: "pointer",
          fontSize: 11,
          fontFamily: "inherit",
          textAlign: "left",
        }}
      >
        {/* Chevron */}
        <svg
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
          style={{
            flexShrink: 0,
            transform: open ? "rotate(90deg)" : "rotate(0deg)",
            transition: reducedMotion ? undefined : "transform 0.15s ease",
          }}
        >
          <polyline points="9 18 15 12 9 6" />
        </svg>
        <span>{t("chat.reasoning")}</span>
        {streaming && !open && (
          <span
            aria-hidden="true"
            style={{ marginLeft: "auto", color: "var(--syn-accent)", fontSize: 10 }}
          >
            {t("chat.thinking")}
          </span>
        )}
      </button>

      {/* ── Expanded content ──────────────────────────────────────────────────── */}
      {open && (
        <div
          style={{
            padding: "8px 10px",
            background: "var(--syn-surface-sunken)",
            color: "var(--syn-text-muted)",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            fontFamily: "ui-monospace, SFMono-Regular, SF Mono, Menlo, Consolas, monospace",
            fontSize: 11,
            lineHeight: 1.5,
            maxHeight: 300,
            overflowY: "auto",
          }}
          aria-live={streaming ? "polite" : undefined}
        >
          {content}
          {streaming && (
            <span
              aria-hidden="true"
              style={{
                display: "inline-block",
                width: 6,
                height: 12,
                background: "var(--syn-accent)",
                marginLeft: 2,
                verticalAlign: "text-bottom",
                animation: "synapse-blink 1s step-end infinite",
              }}
            />
          )}
        </div>
      )}

      {/* ── R7-9: Rolling preview strip (collapsed + streaming only) ─────────── */}
      {showPreview && (
        <div
          data-testid="think-preview"
          aria-label={t("chat.thinkPreviewLabel")}
          aria-live="polite"
          style={{
            padding: "5px 10px",
            background: "var(--syn-surface-sunken)",
            position: "relative",
            overflow: "hidden",
          }}
        >
          <pre
            style={{
              margin: 0,
              fontSize: 10,
              lineHeight: 1.4,
              color: "var(--syn-text-dim)",
              fontFamily:
                "ui-monospace, SFMono-Regular, SF Mono, Menlo, Consolas, monospace",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              opacity: 0.75,
            }}
          >
            {previewText}
          </pre>
          {/* Fade mask — omitted when prefers-reduced-motion */}
          {!reducedMotion && (
            <div
              aria-hidden="true"
              style={{
                position: "absolute",
                bottom: 0,
                left: 0,
                right: 0,
                height: "60%",
                background:
                  "linear-gradient(to bottom, transparent, var(--syn-surface-sunken, var(--syn-bg-soft)))",
                pointerEvents: "none",
              }}
            />
          )}
        </div>
      )}
    </div>
  );
}
