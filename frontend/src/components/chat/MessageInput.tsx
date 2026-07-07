/**
 * MessageInput.tsx — chat composer toolbar (B2 / ADR-0019 §3 / I4).
 *
 * INVARIANT I4: plain <textarea> only — CodeMirror is reserved for the wiki editor,
 *   NOT the chat input. No WYSIWYG, no contentEditable.
 *
 * Features (B2 — mirrors llm_wiki chat-input.tsx):
 *   - Attach image: hidden <input type=file>; base64-encode, strip data-URI prefix,
 *     send as { mime, data_base64 }. Cap: CHAT_MAX_IMAGES=4, CHAT_MAX_IMAGE_BYTES=5MB.
 *     Button DISABLED with tooltip when !supports_vision.
 *     Thumbnails are static (no per-token re-render — I3).
 *   - Web search toggle: button with emerald dot when active → toggles webSearchEnabled.
 *   - Retrieval-mode segmented control: Fast | Standard | Deep | Local first.
 *   - Send/Stop button + provider label + keyboard hints.
 *
 * INVARIANT I3: thumbnails rendered from component state (static after file select).
 *   No subscription to streaming buffers here.
 */

import {
  useState,
  useRef,
  useCallback,
  useEffect,
  type KeyboardEvent,
  type ChangeEvent,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";
import { useProviderStore, selectActiveProvider } from "../../store/providerStore";
import { useStatusStore, selectSupportsVision } from "../../store/statusStore";
import {
  useSettingsStore,
  selectRetrievalMode,
  selectSetRetrievalMode,
  selectWebSearchEnabled,
  selectSetWebSearchEnabled,
  type RetrievalMode,
} from "../../store/settingsStore";
import { showToast } from "../common/Toast";
import type { ChatImageAttachment } from "../../api/chatClient";

// ─── Constants ────────────────────────────────────────────────────────────────

export const CHAT_MAX_IMAGES = 4;
export const CHAT_MAX_IMAGE_BYTES = 5 * 1024 * 1024; // 5 MB

// ─── Props ────────────────────────────────────────────────────────────────────

interface MessageInputProps {
  /**
   * Called when the user submits a message.
   * Receives text + any attached images (base64-encoded, mime stripped).
   */
  onSend: (text: string, images: ChatImageAttachment[]) => void;
  onStop: () => void;
  isStreaming: boolean;
  disabled?: boolean;
  /** Pre-fill text (from ScenarioTemplates, F1). */
  initialValue?: string;
}

// ─── Retrieval mode config ─────────────────────────────────────────────────────

interface ModeOption {
  key: RetrievalMode;
  labelKey: string;
}

const RETRIEVAL_MODES: ModeOption[] = [
  { key: "fast", labelKey: "chat.retrievalMode.fast" },
  { key: "standard", labelKey: "chat.retrievalMode.standard" },
  { key: "deep", labelKey: "chat.retrievalMode.deep" },
  { key: "local_first", labelKey: "chat.retrievalMode.localFirst" },
];

// ─── Thumbnail component ───────────────────────────────────────────────────────

interface ThumbnailProps {
  dataUrl: string;
  index: number;
  onRemove: (index: number) => void;
}

function ImageThumbnail({ dataUrl, index, onRemove }: ThumbnailProps): ReactNode {
  return (
    <div
      style={{
        position: "relative",
        flexShrink: 0,
        width: 48,
        height: 48,
        borderRadius: 4,
        overflow: "hidden",
        border: "1px solid var(--syn-border)",
      }}
    >
      <img
        src={dataUrl}
        alt=""
        aria-hidden="true"
        style={{ width: "100%", height: "100%", objectFit: "cover" }}
      />
      <button
        type="button"
        onClick={() => onRemove(index)}
        aria-label={`Remove image ${index + 1}`}
        style={{
          position: "absolute",
          top: 1,
          right: 1,
          width: 16,
          height: 16,
          borderRadius: "50%",
          background: "rgba(0,0,0,0.65)",
          border: "none",
          cursor: "pointer",
          color: "#fff",
          fontSize: 10,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          lineHeight: 1,
          padding: 0,
        }}
      >
        ×
      </button>
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────

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
  const fileInputRef = useRef<HTMLInputElement>(null);

  // I3: thumbnails stored as static { dataUrl, attachment } pairs — never mutated per token.
  const [attachments, setAttachments] = useState<
    { dataUrl: string; attachment: ChatImageAttachment }[]
  >([]);

  // Provider name for display only (I6 — backend resolves provider for the actual request)
  const activeProvider = useProviderStore(selectActiveProvider);
  const providerLabel = activeProvider
    ? (activeProvider.model_id ?? activeProvider.provider_type)
    : null;

  // Vision capability from GET /status (B2)
  const supportsVision = useStatusStore(selectSupportsVision);

  // Retrieval mode + web search from settingsStore (B2, persisted)
  const retrievalMode = useSettingsStore(selectRetrievalMode);
  const setRetrievalMode = useSettingsStore(selectSetRetrievalMode);
  const webSearchEnabled = useSettingsStore(selectWebSearchEnabled);
  const setWebSearchEnabled = useSettingsStore(selectSetWebSearchEnabled);

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

  // ── Image attachment handling ─────────────────────────────────────────────

  const handleFileChange = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(e.target.files ?? []);
      // Reset the input so the same file can be re-selected later
      e.target.value = "";

      if (attachments.length + files.length > CHAT_MAX_IMAGES) {
        showToast(t("chat.tooManyImages", { max: CHAT_MAX_IMAGES }), "error");
        return;
      }

      const toAdd: { dataUrl: string; attachment: ChatImageAttachment }[] = [];

      const processFile = (file: File): Promise<void> =>
        new Promise((resolve) => {
          if (file.size > CHAT_MAX_IMAGE_BYTES) {
            showToast(
              t("chat.imageTooLarge", { name: file.name, maxMb: CHAT_MAX_IMAGE_BYTES / (1024 * 1024) }),
              "error",
            );
            resolve();
            return;
          }
          const reader = new FileReader();
          reader.onload = (ev) => {
            const dataUrl = ev.target?.result as string;
            // Strip `data:<mime>;base64,` prefix — send raw base64 only
            const base64Start = dataUrl.indexOf(",") + 1;
            const data_base64 = dataUrl.slice(base64Start);
            toAdd.push({
              dataUrl,
              attachment: { mime: file.type, data_base64 },
            });
            resolve();
          };
          reader.readAsDataURL(file);
        });

      void Promise.all(files.map(processFile)).then(() => {
        if (toAdd.length > 0) {
          setAttachments((prev) => {
            const next = [...prev, ...toAdd];
            return next.slice(0, CHAT_MAX_IMAGES);
          });
        }
      });
    },
    [attachments.length, t],
  );

  const handleRemoveAttachment = useCallback((index: number) => {
    setAttachments((prev) => prev.filter((_, i) => i !== index));
  }, []);

  // ── Send ──────────────────────────────────────────────────────────────────

  const handleSend = useCallback(() => {
    const text = value.trim();
    if (!text || isStreaming || disabled) return;
    onSend(text, attachments.map((a) => a.attachment));
    setValue("");
    setAttachments([]);
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  }, [value, isStreaming, disabled, onSend, attachments]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        if (e.repeat) return;
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend],
  );

  const isInputDisabled = disabled || isStreaming;

  // ── Web search toggle ──────────────────────────────────────────────────────

  const handleToggleWebSearch = useCallback(() => {
    setWebSearchEnabled(!webSearchEnabled);
  }, [webSearchEnabled, setWebSearchEnabled]);

  // ── Attach image button click ──────────────────────────────────────────────

  const handleAttachClick = useCallback(() => {
    if (!supportsVision || isInputDisabled) return;
    fileInputRef.current?.click();
  }, [supportsVision, isInputDisabled]);

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

      {/* Composer toolbar: attach + web search + retrieval mode */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          flexWrap: "wrap",
        }}
      >
        {/* Attach image button (B2) */}
        <button
          type="button"
          data-testid="attach-image-btn"
          onClick={handleAttachClick}
          disabled={!supportsVision || isInputDisabled}
          title={
            !supportsVision
              ? t("chat.attachImageDisabled")
              : t("chat.attachImage")
          }
          aria-label={
            !supportsVision
              ? t("chat.attachImageDisabled")
              : t("chat.attachImage")
          }
          style={{
            background: "none",
            border: "1px solid var(--syn-border)",
            borderRadius: 6,
            padding: "4px 8px",
            cursor: !supportsVision || isInputDisabled ? "not-allowed" : "pointer",
            color: !supportsVision ? "var(--syn-text-dim)" : "var(--syn-text-muted)",
            display: "flex",
            alignItems: "center",
            gap: 4,
            fontSize: 12,
            opacity: !supportsVision ? 0.45 : 1,
            transition: "opacity 0.15s ease",
          }}
        >
          {/* Image icon */}
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
            <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
            <circle cx="8.5" cy="8.5" r="1.5" />
            <polyline points="21 15 16 10 5 21" />
          </svg>
        </button>

        {/* Hidden file input — activated by the attach button */}
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          multiple
          data-testid="attach-image-input"
          style={{ display: "none" }}
          onChange={handleFileChange}
        />

        {/* Web search toggle (B2) */}
        <button
          type="button"
          data-testid="web-search-toggle"
          onClick={handleToggleWebSearch}
          disabled={isInputDisabled}
          title={webSearchEnabled ? t("chat.webSearchOn") : t("chat.webSearchOff")}
          aria-label={webSearchEnabled ? t("chat.webSearchOn") : t("chat.webSearchOff")}
          aria-pressed={webSearchEnabled}
          style={{
            background: webSearchEnabled ? "var(--syn-accent-soft)" : "none",
            border: "1px solid",
            borderColor: webSearchEnabled ? "var(--syn-accent)" : "var(--syn-border)",
            borderRadius: 6,
            padding: "4px 8px",
            cursor: isInputDisabled ? "not-allowed" : "pointer",
            color: webSearchEnabled ? "var(--syn-accent)" : "var(--syn-text-muted)",
            display: "flex",
            alignItems: "center",
            gap: 4,
            fontSize: 12,
            transition: "background 0.15s ease, border-color 0.15s ease, color 0.15s ease",
          }}
        >
          {/* Status dot: emerald when active */}
          <span
            style={{
              width: 7,
              height: 7,
              borderRadius: "50%",
              background: webSearchEnabled ? "#10b981" : "var(--syn-text-dim)",
              display: "inline-block",
              flexShrink: 0,
              transition: "background 0.15s ease",
            }}
            aria-hidden="true"
          />
          {t("chat.webSearch")}
        </button>

        {/* Divider: separates the action/toggle cluster (attach, Web) from the
            single-select retrieval-mode control so the two don't read as one group. */}
        <span
          aria-hidden="true"
          style={{ width: 1, alignSelf: "stretch", margin: "2px 2px", background: "var(--syn-border)" }}
        />

        {/* Retrieval mode segmented control (B2) — single-select ⇒ radiogroup */}
        <div
          role="radiogroup"
          aria-label={t("chat.retrievalModeLabel")}
          style={{
            display: "flex",
            borderRadius: 6,
            overflow: "hidden",
            border: "1px solid var(--syn-border)",
          }}
        >
          {RETRIEVAL_MODES.map((mode, i) => {
            const isActive = retrievalMode === mode.key;
            return (
              <button
                key={mode.key}
                type="button"
                data-testid={`retrieval-mode-${mode.key}`}
                onClick={() => setRetrievalMode(mode.key)}
                disabled={isInputDisabled}
                role="radio"
                aria-checked={isActive}
                title={t(mode.labelKey)}
                style={{
                  padding: "4px 8px",
                  fontSize: 11,
                  fontWeight: isActive ? 600 : 400,
                  background: isActive ? "var(--syn-accent)" : "none",
                  color: isActive ? "#fff" : "var(--syn-text-muted)",
                  border: "none",
                  borderRight:
                    i < RETRIEVAL_MODES.length - 1
                      ? "1px solid var(--syn-border)"
                      : "none",
                  cursor: isInputDisabled ? "not-allowed" : "pointer",
                  transition: "background 0.12s ease, color 0.12s ease",
                  whiteSpace: "nowrap",
                }}
              >
                {t(mode.labelKey)}
              </button>
            );
          })}
        </div>
      </div>

      {/* Image thumbnails row (I3: static, no per-token re-render) */}
      {attachments.length > 0 && (
        <div
          data-testid="image-thumbnails"
          style={{
            display: "flex",
            gap: 6,
            flexWrap: "wrap",
          }}
        >
          {attachments.map((att, i) => (
            <ImageThumbnail
              key={i}
              dataUrl={att.dataUrl}
              index={i}
              onRemove={handleRemoveAttachment}
            />
          ))}
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
          placeholder={
            isStreaming ? t("chat.inputPlaceholderStreaming") : t("chat.inputPlaceholder")
          }
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
            <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
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
              background:
                value.trim() && !isInputDisabled ? "var(--syn-accent)" : "var(--syn-border)",
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

      <div style={{ fontSize: 11, color: "var(--syn-text-dim)" }}>{t("chat.inputHint")}</div>
    </div>
  );
}
