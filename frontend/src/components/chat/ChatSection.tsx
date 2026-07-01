/**
 * ChatSection.tsx — section root for the Chat area (ADR-0019 §3 / F6 / M4 Phase 3).
 *
 * Layout:
 *   [ ConversationList 220px ] | [ MessageList (flex 1) ]
 *                                [ MessageInput            ]
 *
 * Responsibilities:
 *   - Wires MessageInput.onSend → useChatStream.send (building the ChatStreamRequest).
 *   - Wires Stop button → useChatStream.abort.
 *   - Wires Regenerate → re-POST with regenerate:true (AC-F6-4).
 *   - Inserts the user message into the store immediately (optimistic, before stream).
 *   - Does NOT include provider_type / model_id in the request body (I6).
 *
 * The active vaultId comes from graphStore (shared selection key).
 * Context window comes from settingsStore (F14).
 */

import { useCallback, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useChatStore, selectActiveConversationId, selectIsStreaming, selectMessages } from "../../store/chatStore";
import type { ChatMessage } from "../../store/chatStore";
import { useGraphStore, selectVaultId, selectSetActiveSection } from "../../store/graphStore";
import { useSettingsStore, selectContextWindow, selectConversationHistoryLength } from "../../store/settingsStore";
import { useChatStream } from "./useChatStream";
import { buildMessagePayload } from "./buildMessagePayload";
import { ConversationList } from "./ConversationList";
import { MessageList } from "./MessageList";
import { MessageInput } from "./MessageInput";
import { EmptyState } from "../common/EmptyState";
import { useProviderConfigured } from "../../hooks/useProviderConfigured";
import type { ChatStreamRequest } from "../../api/chatClient";

export function ChatSection(): ReactNode {
  const { t } = useTranslation();
  const vaultId = useGraphStore(selectVaultId);
  const setActiveSection = useGraphStore(selectSetActiveSection);
  const activeConversationId = useChatStore(selectActiveConversationId);
  const isStreaming = useChatStore(selectIsStreaming);
  const messages = useChatStore(selectMessages);
  const appendMessage = useChatStore((s) => s.appendMessage);
  const contextWindow = useSettingsStore(selectContextWindow);
  const historyLength = useSettingsStore(selectConversationHistoryLength);

  const { send, abort } = useChatStream();

  // Provider gate (P0): check once on mount whether a provider is configured.
  // Show nothing until resolved (no flicker), then either the gate or the normal view.
  const { configured, loading: providerLoading } = useProviderConfigured();

  const handleSend = useCallback(
    (text: string) => {
      if (isStreaming) return;

      // Optimistic: insert the user message immediately
      const userMsg: ChatMessage = {
        id: crypto.randomUUID(),
        conversation_id: activeConversationId ?? "",
        role: "user",
        content: text,
        input_tokens: 0,
        output_tokens: 0,
        total_cost_usd: 0,
        created_at: new Date().toISOString(),
        citations: [],
      };
      appendMessage(userMsg);

      // Build message history to send (include existing settled messages + this new one),
      // then trim to historyLength (I7 context-budget enforcement, AC-HARD-CONV-2).
      const allMessages = [
        ...messages.map((m) => ({ role: m.role, content: m.content })),
        { role: "user" as const, content: text },
      ];
      const history = buildMessagePayload(allMessages, historyLength);

      const req: ChatStreamRequest = {
        conversation_id: activeConversationId,
        messages: history,
        vault_id: vaultId,
        context_window: contextWindow > 0 ? contextWindow : null,
        operation: "chat",
      };

      send(req);
    },
    [isStreaming, activeConversationId, messages, vaultId, contextWindow, historyLength, appendMessage, send],
  );

  const handleRegenerate = useCallback(() => {
    if (isStreaming) return;
    // Find the last user message to re-send
    const lastUserIdx = messages.slice().reverse().findIndex((m) => m.role === "user");
    if (lastUserIdx === -1) return;
    const lastUser = messages[messages.length - 1 - lastUserIdx];
    if (!lastUser) return;

    // Build history up to (and including) the last user message,
    // then trim to historyLength (I7 context-budget enforcement, AC-HARD-CONV-2).
    const allUpToUser = messages
      .slice(0, messages.length - lastUserIdx)
      .map((m) => ({ role: m.role, content: m.content }));
    const historyUpToUser = buildMessagePayload(allUpToUser, historyLength);

    const req: ChatStreamRequest = {
      conversation_id: activeConversationId,
      messages: historyUpToUser,
      vault_id: vaultId,
      context_window: contextWindow > 0 ? contextWindow : null,
      operation: "chat",
      regenerate: true,
    };

    send(req);
  }, [isStreaming, messages, activeConversationId, vaultId, contextWindow, historyLength, send]);

  // While checking configuration, render nothing to avoid flicker (I3).
  if (providerLoading || configured === null) {
    return (
      <div
        style={{ flex: 1, display: "flex", width: "100%", height: "100%", background: "var(--syn-bg)" }}
        data-testid="section-chat"
      />
    );
  }

  // Gate: no provider configured → block with CTA.
  if (!configured) {
    return (
      <div
        style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", width: "100%", height: "100%", background: "var(--syn-bg)" }}
        data-testid="section-chat"
      >
        <EmptyState
          title={t("providerGate.title")}
          body={t("providerGate.body")}
          testId="provider-gate-chat"
          actions={[
            {
              label: t("providerGate.cta"),
              variant: "primary",
              onClick: () => setActiveSection("settings"),
            },
          ]}
        />
      </div>
    );
  }

  return (
    <div
      style={{
        display: "flex",
        flex: 1,
        overflow: "hidden",
        width: "100%",
        height: "100%",
        background: "var(--syn-bg)",
      }}
      data-testid="section-chat"
    >
      {/* Left: conversation list */}
      <div
        style={{
          width: 220,
          flexShrink: 0,
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
        }}
      >
        <ConversationList />
      </div>

      {/* Center: message area */}
      <div
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          minWidth: 0,
        }}
      >
        {/* Header */}
        <div
          style={{
            padding: "10px 16px",
            borderBottom: "1px solid var(--syn-border)",
            fontSize: 13,
            color: "var(--syn-text-muted)",
            flexShrink: 0,
          }}
        >
          {t("chat.title")}
        </div>

        {/* Message list (virtualized, I4) */}
        <MessageList onRegenerate={handleRegenerate} />

        {/* Input (plain textarea, I4) */}
        <MessageInput
          onSend={handleSend}
          onStop={abort}
          isStreaming={isStreaming}
        />
      </div>
    </div>
  );
}
