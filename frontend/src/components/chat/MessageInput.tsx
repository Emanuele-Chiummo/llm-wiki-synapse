/**
 * MessageInput.tsx — plain <textarea> chat input (ADR-0019 §3 / I4).
 *
 * INVARIANT I4: plain <textarea> only — CodeMirror is reserved for the wiki editor,
 *   NOT the chat input. No WYSIWYG, no contentEditable.
 *
 * - Enter = send, Shift+Enter = newline (ADR-0019 §3).
 * - Disabled while streaming; shows a Stop button while streaming.
 * - Shows the active provider name (from providerStore) for display only (I6).
 * - Does NOT send provider_type / model_id in the request body (I6).
 * - Pre-fillable by ScenarioTemplates (F1) via the `initialValue` prop.
 */

import { useState, useRef, useCallback, useEffect, type KeyboardEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useProviderStore, selectActiveProvider } from "../../store/providerStore";

interface MessageInputProps {
  onSend: (text: string) => void;
  onStop: () => void;
  isStreaming: boolean;
  disabled?: boolean;
  /** Pre-fill text (from ScenarioTemplates, F1). */
  initialValue?: string;
}

export function MessageInput({
  onSend,
  onStop,
  isStreaming,
  disabled = false,
  initialValue,
}: MessageInputProps): ReactNode {
  const { t } = useTranslation();
  const [value, setValue] = useState(initialValue ?? "");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Provider name for display only (I6 — backend resolves provider for the actual request)
  const activeProvider = useProviderStore(selectActiveProvider);
  const providerLabel = activeProvider
    ? (activeProvider.model_id ?? activeProvider.provider_type)
    : null;

  // Sync external initial value (ScenarioTemplates)
  useEffect(() => {
    if (initialValue !== undefined) {
      setValue(initialValue);
      textareaRef.current?.focus();
    }
  }, [initialValue]);

  // Auto-resize textarea
  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 200) + "px";
  }, [value]);

  const handleSend = useCallback(() => {
    const text = value.trim();
    if (!text || isStreaming || disabled) return;
    onSend(text);
    setValue("");
    // Reset height
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  }, [value, isStreaming, disabled, onSend]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend],
  );

  const isInputDisabled = disabled || isStreaming;

  return (
    <div
      style={{
        borderTop: "1px solid var(--syn-border)",
        padding: "12px 16px",
        background: "var(--syn-surface-sunken)",
        display: "flex",
        flexDirection: "column",
        gap: 8,
      }}
    >
      {/* Provider indicator (display only — I6) */}
      {providerLabel && (
        <div
          style={{
            fontSize: 11,
            color: "var(--syn-text-dim)",
            display: "flex",
            alignItems: "center",
            gap: 4,
          }}
          aria-label={t("chat.usingProvider", { provider: providerLabel })}
        >
          <span style={{ color: "var(--syn-green)", fontSize: 8 }}>●</span>
          <span>{providerLabel}</span>
        </div>
      )}

      <div style={{ display: "flex", gap: 8, alignItems: "flex-end" }}>
        {/* I4: plain <textarea> — NOT CodeMirror, no WYSIWYG */}
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={isInputDisabled}
          placeholder={isStreaming ? t("chat.inputPlaceholderStreaming") : t("chat.inputPlaceholder")}
          rows={1}
          aria-label={t("chat.inputLabel")}
          className="chat-input-textarea"
          style={{
            flex: 1,
            resize: "none",
            background: "var(--syn-bg)",
            border: "1px solid var(--syn-border)",
            borderRadius: 6,
            color: isInputDisabled ? "var(--syn-text-dim)" : "var(--syn-text)",
            fontSize: 14,
            lineHeight: 1.5,
            padding: "8px 12px",
            fontFamily: "inherit",
            outline: "none",
            minHeight: 38,
            maxHeight: 200,
            overflowY: "auto",
            transition: "border-color 0.15s ease",
          }}
          onFocus={(e) => {
            if (!isInputDisabled) e.currentTarget.style.borderColor = "var(--syn-accent)";
          }}
          onBlur={(e) => {
            e.currentTarget.style.borderColor = "var(--syn-border)";
          }}
        />

        {/* Stop button (shown while streaming) */}
        {isStreaming ? (
          <button
            type="button"
            onClick={onStop}
            aria-label={t("chat.stop")}
            title={t("chat.stop")}
            className="chat-stop-btn"
            style={{
              flexShrink: 0,
              padding: "8px 14px",
              background: "var(--syn-red)",
              border: "none",
              borderRadius: 6,
              color: "#fff",
              cursor: "pointer",
              fontSize: 13,
              fontWeight: 600,
              height: 38,
              display: "flex",
              alignItems: "center",
              gap: 4,
            }}
          >
            {/* Stop icon */}
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="currentColor"
              aria-hidden="true"
            >
              <rect x="3" y="3" width="18" height="18" rx="2" />
            </svg>
            {t("chat.stop")}
          </button>
        ) : (
          /* Send button */
          <button
            type="button"
            onClick={handleSend}
            disabled={isInputDisabled || value.trim() === ""}
            aria-label={t("chat.send")}
            title={t("chat.send")}
            className="chat-send-btn"
            style={{
              flexShrink: 0,
              padding: "8px 14px",
              background: value.trim() && !isInputDisabled ? "var(--syn-accent)" : "var(--syn-border)",
              border: "none",
              borderRadius: 6,
              color: value.trim() && !isInputDisabled ? "#fff" : "var(--syn-text-dim)",
              cursor: value.trim() && !isInputDisabled ? "pointer" : "not-allowed",
              fontSize: 13,
              fontWeight: 600,
              height: 38,
              display: "flex",
              alignItems: "center",
              gap: 4,
              transition: "background 0.15s ease, color 0.15s ease",
            }}
          >
            {/* Send icon */}
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <line x1="22" y1="2" x2="11" y2="13" />
              <polygon points="22 2 15 22 11 13 2 9 22 2" />
            </svg>
            {t("chat.send")}
          </button>
        )}
      </div>

      <div style={{ fontSize: 11, color: "var(--syn-text-dim)" }}>
        {t("chat.inputHint")}
      </div>
    </div>
  );
}
