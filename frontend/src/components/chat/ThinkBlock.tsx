/**
 * ThinkBlock.tsx — collapsible reasoning block (F7 / ADR-0019 §2.4).
 *
 * Used in TWO modes:
 *   1. Streaming: inside <StreamingMessage>, receives live `content` from the streaming store.
 *   2. Settled: re-derived from the stored raw assistant message (pure string split).
 *
 * Collapsed by default (AC-F7-1).
 * Content is the raw think text (plain, no markdown parse — I3).
 * No regex-per-token on the client — the server already split think vs. token events.
 */

import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

interface ThinkBlockProps {
  /** Raw reasoning text (no parse needed — displayed as pre-wrap). */
  content: string;
  /** True while the think stream is still live (shows a cursor). */
  streaming?: boolean;
}

export function ThinkBlock({ content, streaming = false }: ThinkBlockProps): ReactNode {
  const [open, setOpen] = useState(false);
  const { t } = useTranslation();

  if (!content) return null;

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
          borderBottom: open ? "1px solid var(--syn-border)" : "none",
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
            transition: "transform 0.15s ease",
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
    </div>
  );
}
