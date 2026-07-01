/**
 * MessageList.tsx — virtualized message list (ADR-0019 §3 / I3 / I4 / AC-F6-6).
 *
 * INVARIANT I4 / AC-F6-6: ≤30 mounted DOM rows regardless of message count.
 *   TanStack Virtual (useVirtualizer) is used exclusively — no windowing library swap.
 *
 * INVARIANT I3 / AC-G3-4:
 *   - This component subscribes to `messages` (settled, immutable array) via useMessages().
 *   - It does NOT subscribe to streamingContent or streamingThink.
 *   - Adding a new token to the streaming buffer does NOT re-render this component.
 *   - Only StreamingMessage reads the streaming buffers.
 *
 * Layout:
 *   - Settled messages: role-labelled rows, rendered by MarkdownView.
 *   - The in-flight assistant turn: rendered by StreamingMessage (appended below settled list).
 *   - Auto-scroll to bottom on new message / streaming append (scroll-to-last-row).
 *   - Save-to-wiki button on assistant messages (F6 AC-F6-5): enabled (M5).
 *     On click: POST /ingest/from-text → shows success (page_title + wikilink) or error.
 *   - Cost display per turn (I7): 4dp from total_cost_usd on the done event.
 *   - Regenerate button on the last assistant message (AC-F6-4).
 */

import {
  useRef,
  useEffect,
  useState,
  useCallback,
  memo,
  type ReactNode,
} from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useTranslation } from "react-i18next";
import {
  useChatStore,
  useMessages,
  selectIsStreaming,
  selectLastUsage,
  selectActiveConversationId,
} from "../../store/chatStore";
import type { ChatMessage } from "../../store/chatStore";
import { useGraphStore, selectVaultId } from "../../store/graphStore";
import { saveToWikiV2 } from "../../api/chatClient";
import { showToast } from "../common/Toast";
import { MarkdownView } from "./MarkdownView";
import { StreamingMessage } from "./StreamingMessage";

interface MessageListProps {
  onRegenerate?: () => void;
}

export function MessageList({ onRegenerate }: MessageListProps): ReactNode {
  const { t } = useTranslation();
  // Settled messages only — NOT subscribing to streaming buffers (AC-G3-4)
  const messages = useMessages();
  const isStreaming = useChatStore(selectIsStreaming);
  const lastUsage = useChatStore(selectLastUsage);
  const activeConversationId = useChatStore(selectActiveConversationId);
  const vaultId = useGraphStore(selectVaultId);

  const parentRef = useRef<HTMLDivElement>(null);

  const virtualizer = useVirtualizer({
    count: messages.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 120,
    overscan: 5,
  });

  // Auto-scroll to bottom when messages change or streaming starts
  useEffect(() => {
    if (!parentRef.current) return;
    const el = parentRef.current;
    el.scrollTop = el.scrollHeight;
  }, [messages.length, isStreaming]);

  return (
    <div
      ref={parentRef}
      style={{
        flex: 1,
        overflowY: "auto",
        overflowX: "hidden",
        padding: "0 0 8px 0",
      }}
      data-testid="message-list"
    >
      {messages.length === 0 && !isStreaming && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            height: "100%",
            color: "var(--syn-text-dim)",
            fontSize: 14,
          }}
        >
          {t("chat.empty")}
        </div>
      )}

      {/* Virtualized settled messages */}
      {messages.length > 0 && (
        <div
          style={{
            height: virtualizer.getTotalSize(),
            width: "100%",
            position: "relative",
          }}
        >
          {virtualizer.getVirtualItems().map((virtualItem) => {
            const msg = messages[virtualItem.index];
            if (!msg) return null;
            const isLast = virtualItem.index === messages.length - 1;
            return (
              <div
                key={virtualItem.key}
                data-index={virtualItem.index}
                ref={virtualizer.measureElement}
                style={{
                  position: "absolute",
                  top: 0,
                  left: 0,
                  width: "100%",
                  transform: `translateY(${virtualItem.start}px)`,
                  padding: "12px 16px",
                  borderBottom: "1px solid var(--syn-border)",
                }}
              >
                <MessageRow
                  msg={msg}
                  msgIndex={virtualItem.index}
                  allMessages={messages}
                  isLast={isLast}
                  onRegenerate={isLast && msg.role === "assistant" ? onRegenerate : undefined}
                  showCost={isLast && msg.role === "assistant" && lastUsage !== null}
                  costUsd={isLast ? (lastUsage?.totalCostUsd ?? msg.total_cost_usd) : msg.total_cost_usd}
                  vaultId={vaultId}
                  conversationId={activeConversationId}
                  t={t}
                />
              </div>
            );
          })}
        </div>
      )}

      {/* In-flight streaming turn — NOT inside the virtualizer (AC-G3-4) */}
      {isStreaming && (
        <div
          style={{
            padding: "12px 16px",
            borderBottom: "1px solid var(--syn-border)",
          }}
        >
          <MessageRoleLabel role="assistant" t={t} />
          <StreamingMessage />
        </div>
      )}
    </div>
  );
}

// ─── Save-to-wiki state ───────────────────────────────────────────────────────

type SaveState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "success"; pageId: string; filePath: string }
  | { kind: "error"; message: string };

/**
 * Derive a page title from the user message that prompted this assistant reply.
 * Walks backward from `msgIndex` in `allMessages` to find the nearest "user" role
 * message. Falls back to the first line of the assistant content, then to a generic
 * fallback string. Trims to 80 chars max.
 */
function deriveSaveTitle(
  msg: ChatMessage,
  msgIndex: number,
  allMessages: ChatMessage[],
): string {
  // Search backwards for a user message preceding this assistant message
  for (let i = msgIndex - 1; i >= 0; i--) {
    const candidate = allMessages[i];
    if (candidate?.role === "user") {
      const trimmed = candidate.content.trim().replace(/\s+/g, " ");
      return trimmed.length > 80 ? trimmed.slice(0, 80) : trimmed;
    }
  }
  // Fallback: first line of the assistant content (strip <think> preamble)
  const firstLine = msg.content
    .replace(/<think>[\s\S]*?<\/think>/gi, "")
    .trim()
    .split("\n")[0]
    ?.trim() ?? "";
  return firstLine.length > 80 ? firstLine.slice(0, 80) : firstLine || "Saved answer";
}

// ─── MessageRow — memoized per settled message ────────────────────────────────

interface MessageRowProps {
  msg: ChatMessage;
  /** Index of this message in the allMessages array — used to walk back to user question. */
  msgIndex: number;
  /** Full settled messages array — needed to derive the save title from prior user msg. */
  allMessages: ChatMessage[];
  isLast: boolean;
  onRegenerate?: (() => void) | undefined;
  showCost: boolean;
  costUsd: number;
  vaultId: string | null | undefined;
  conversationId: string | null | undefined;
  t: ReturnType<typeof useTranslation>["t"];
}

const MessageRow = memo(function MessageRow({
  msg,
  msgIndex,
  allMessages,
  isLast,
  onRegenerate,
  showCost,
  costUsd,
  vaultId,
  conversationId,
  t,
}: MessageRowProps): ReactNode {
  const [saveState, setSaveState] = useState<SaveState>({ kind: "idle" });

  const handleSaveToWiki = useCallback(async () => {
    if (saveState.kind === "loading") return;
    setSaveState({ kind: "loading" });
    try {
      // Derive title from the user question preceding this assistant message (AC-F6-5)
      const title = deriveSaveTitle(msg, msgIndex, allMessages);
      // Collect source page-ids from citations if available
      const sources =
        msg.citations && msg.citations.length > 0
          ? msg.citations.map((c) => c.id)
          : undefined;
      const result = await saveToWikiV2({
        title,
        content: msg.content,
        vault_id: vaultId ?? null,
        sources,
        conversation_id: conversationId ?? null,
      });
      setSaveState({ kind: "success", pageId: result.page_id, filePath: result.file_path });
      // Success toast — i18n IT/EN (F16)
      showToast(t("chat.saveToWikiSavedToast", { path: result.file_path }), "success");
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : t("chat.saveToWikiError");
      setSaveState({ kind: "error", message });
      showToast(t("chat.saveToWikiErrorToast"), "error");
    }
  }, [saveState.kind, msg, msgIndex, allMessages, vaultId, conversationId, t]);

  return (
    <div>
      <MessageRoleLabel role={msg.role} t={t} />
      {/* Pass citations to MarkdownView for [n] decoration (ADR-0022 §2.4).
          onCitationClick is omitted — page navigation stub; TODO(F5-nav): wire when
          wiki tree selection is wired to the chat panel. */}
      <MarkdownView
        content={msg.content}
        citations={msg.citations}
        /* onCitationClick intentionally omitted — navigation not yet wired (F5-nav stub) */
      />

      {/* Metadata footer — cost + actions */}
      {(showCost || isLast) && msg.role === "assistant" && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            flexWrap: "wrap",
            gap: 12,
            marginTop: 8,
            fontSize: 11,
            color: "var(--syn-text-dim)",
          }}
        >
          {/* I7: cost displayed at 4dp */}
          {costUsd > 0 && (
            <span aria-label={t("chat.cost")}>
              {t("chat.costLabel", { cost: costUsd.toFixed(4) })}
            </span>
          )}

          {/* Save to wiki (AC-F6-5) — wired to POST /chat/save-to-wiki (v0.6) */}
          {saveState.kind === "idle" || saveState.kind === "error" ? (
            <button
              type="button"
              onClick={() => void handleSaveToWiki()}
              data-testid="save-to-wiki-btn"
              disabled={saveState.kind === "loading"}
              style={{
                background: "none",
                border: "1px solid var(--syn-border)",
                borderRadius: 4,
                color: "var(--syn-text-muted)",
                cursor: "pointer",
                fontSize: 11,
                padding: "2px 8px",
              }}
              title={t("chat.saveToWiki")}
            >
              {t("chat.saveToWiki")}
            </button>
          ) : saveState.kind === "loading" ? (
            <button
              type="button"
              data-testid="save-to-wiki-btn"
              disabled
              style={{
                background: "none",
                border: "1px solid var(--syn-border)",
                borderRadius: 4,
                color: "var(--syn-text-muted)",
                cursor: "not-allowed",
                fontSize: 11,
                padding: "2px 8px",
                opacity: 0.5,
              }}
            >
              {t("chat.saveToWikiSaving")}
            </button>
          ) : (
            /* success */
            <span
              data-testid="save-to-wiki-success"
              style={{ color: "var(--syn-green)", fontSize: 11 }}
              title={saveState.filePath}
            >
              {t("chat.saveToWikiSaved", { path: saveState.filePath })}
            </span>
          )}

          {/* Inline error — shown below the button on next render */}
          {saveState.kind === "error" && (
            <span
              data-testid="save-to-wiki-error"
              style={{ color: "var(--syn-red)", fontSize: 11 }}
            >
              {saveState.message}
            </span>
          )}

          {/* Regenerate (AC-F6-4) — only on last assistant message */}
          {isLast && onRegenerate && (
            <button
              type="button"
              onClick={onRegenerate}
              style={{
                background: "none",
                border: "1px solid var(--syn-border)",
                borderRadius: 4,
                color: "var(--syn-text-muted)",
                cursor: "pointer",
                fontSize: 11,
                padding: "2px 8px",
              }}
              title={t("chat.regenerate")}
            >
              {t("chat.regenerate")}
            </button>
          )}
        </div>
      )}
    </div>
  );
});

// ─── Role label ───────────────────────────────────────────────────────────────

function MessageRoleLabel({
  role,
  t,
}: {
  role: string;
  t: ReturnType<typeof useTranslation>["t"];
}): ReactNode {
  const isUser = role === "user";
  return (
    <div
      style={{
        fontSize: 11,
        fontWeight: 600,
        color: isUser ? "var(--syn-accent)" : "var(--syn-green)",
        marginBottom: 4,
        textTransform: "uppercase",
        letterSpacing: "0.05em",
      }}
    >
      {isUser ? t("chat.roleUser") : t("chat.roleAssistant")}
    </div>
  );
}
