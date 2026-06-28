/**
 * chatStore.ts — Zustand store for Chat (ADR-0019 §3 / F6 / I3).
 *
 * INVARIANT I3:
 *   - SEPARATE from graphStore/providerStore/ingestStore/settingsStore.
 *     Streaming never re-renders the graph, tree, ingest list, or settings.
 *   - Only `streamingContent` and `streamingThink` mutate per token.
 *   - Only <StreamingMessage> subscribes to those two fields via selectStreamingContent /
 *     selectStreamingThink. The settled `messages` array and MessageList virtualizer do NOT.
 *   - No selector derives parsed markdown from the streaming buffer.
 *     Parse count during a stream of N tokens = 0; exactly 1 after done (AC-G3-2/3/4).
 *   - All collections use shallow equality (useShallow) at the call site.
 *
 * Store shape matches ADR-0019 §3 exactly — do not add fields without updating the ADR.
 */

import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";

// ─── Domain types ─────────────────────────────────────────────────────────────

export interface ConversationSummary {
  id: string;
  vault_id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export type ChatRole = "user" | "assistant" | "system";

export interface ChatMessage {
  id: string;
  conversation_id: string;
  role: ChatRole;
  /** Raw content — includes literal <think>…</think> spans if present (AC-F7-2). */
  content: string;
  input_tokens: number;
  output_tokens: number;
  total_cost_usd: number;
  created_at: string;
}

export interface LastUsage {
  inputTokens: number;
  outputTokens: number;
  totalCostUsd: number;
}

// ─── State shape (ADR-0019 §3) ────────────────────────────────────────────────

export interface ChatState {
  conversations: ConversationSummary[];
  activeConversationId: string | null;
  /** Settled messages for the active conversation (immutable once appended). */
  messages: ChatMessage[];
  // ── streaming buffers — the ONLY fields that mutate per token ──
  /** Raw visible-text buffer (append-only during stream; no parse). */
  streamingContent: string;
  /** Raw reasoning buffer (append-only during stream; no parse). */
  streamingThink: string;
  isStreaming: boolean;
  streamError: string | null;
  lastUsage: LastUsage | null;
  conversationsLoading: boolean;
  conversationsError: string | null;
  messagesLoading: boolean;
  messagesError: string | null;
}

// ─── Actions ──────────────────────────────────────────────────────────────────

export interface ChatActions {
  // Conversation management
  setConversations: (list: ConversationSummary[]) => void;
  setActiveConversationId: (id: string | null) => void;
  addConversation: (conv: ConversationSummary) => void;
  removeConversation: (id: string) => void;
  setConversationsLoading: (loading: boolean) => void;
  setConversationsError: (error: string | null) => void;

  // Message management
  setMessages: (messages: ChatMessage[]) => void;
  appendMessage: (msg: ChatMessage) => void;
  setMessagesLoading: (loading: boolean) => void;
  setMessagesError: (error: string | null) => void;

  // Streaming — called per NDJSON event (I3: cheap string appends only)
  appendToken: (delta: string) => void;
  appendThink: (delta: string) => void;
  setIsStreaming: (v: boolean) => void;
  setStreamError: (error: string | null) => void;

  /**
   * Finalise the streaming turn on `done`:
   * - Append the completed assistant message to `messages` (settled).
   * - Set lastUsage from the done event.
   * - Clear streaming buffers and set isStreaming=false.
   * The caller is responsible for parse (MarkdownView) — NOT this action.
   */
  finalizeTurn: (msg: ChatMessage, usage: LastUsage) => void;

  /** Clear streaming state without persisting a message (on abort / error). */
  clearStream: () => void;
}

export type ChatStore = ChatState & ChatActions;

// ─── Initial state ────────────────────────────────────────────────────────────

const INITIAL: ChatState = {
  conversations: [],
  activeConversationId: null,
  messages: [],
  streamingContent: "",
  streamingThink: "",
  isStreaming: false,
  streamError: null,
  lastUsage: null,
  conversationsLoading: false,
  conversationsError: null,
  messagesLoading: false,
  messagesError: null,
};

// ─── Store ────────────────────────────────────────────────────────────────────

export const useChatStore = create<ChatStore>((set) => ({
  ...INITIAL,

  setConversations: (conversations) => set({ conversations }),
  setActiveConversationId: (activeConversationId) => set({ activeConversationId }),
  addConversation: (conv) => set((s) => ({ conversations: [conv, ...s.conversations] })),
  removeConversation: (id) =>
    set((s) => ({
      conversations: s.conversations.filter((c) => c.id !== id),
      activeConversationId: s.activeConversationId === id ? null : s.activeConversationId,
    })),
  setConversationsLoading: (conversationsLoading) => set({ conversationsLoading }),
  setConversationsError: (conversationsError) => set({ conversationsError }),

  setMessages: (messages) => set({ messages }),
  appendMessage: (msg) => set((s) => ({ messages: [...s.messages, msg] })),
  setMessagesLoading: (messagesLoading) => set({ messagesLoading }),
  setMessagesError: (messagesError) => set({ messagesError }),

  // ── Per-token mutations (cheap string appends, I3) ────────────────────────
  appendToken: (delta) => set((s) => ({ streamingContent: s.streamingContent + delta })),
  appendThink: (delta) => set((s) => ({ streamingThink: s.streamingThink + delta })),
  setIsStreaming: (isStreaming) => set({ isStreaming }),
  setStreamError: (streamError) => set({ streamError }),

  finalizeTurn: (msg, usage) =>
    set((s) => ({
      messages: [...s.messages, msg],
      streamingContent: "",
      streamingThink: "",
      isStreaming: false,
      streamError: null,
      lastUsage: usage,
      activeConversationId: s.activeConversationId ?? msg.conversation_id,
    })),

  clearStream: () =>
    set({
      streamingContent: "",
      streamingThink: "",
      isStreaming: false,
    }),
}));

// ─── Typed selectors (I3) — import these in components, never the raw store ───

// Scalars — Object.is comparison, no useShallow needed
export const selectActiveConversationId = (s: ChatStore): string | null =>
  s.activeConversationId;
export const selectIsStreaming = (s: ChatStore): boolean => s.isStreaming;
export const selectStreamError = (s: ChatStore): string | null => s.streamError;
export const selectLastUsage = (s: ChatStore): LastUsage | null => s.lastUsage;
export const selectConversationsLoading = (s: ChatStore): boolean => s.conversationsLoading;
export const selectConversationsError = (s: ChatStore): string | null => s.conversationsError;
export const selectMessagesLoading = (s: ChatStore): boolean => s.messagesLoading;
export const selectMessagesError = (s: ChatStore): string | null => s.messagesError;

/**
 * selectStreamingContent — subscribed ONLY by <StreamingMessage>.
 * Do NOT use in MessageList or any settled-message component (AC-G3-4).
 */
export const selectStreamingContent = (s: ChatStore): string => s.streamingContent;

/**
 * selectStreamingThink — subscribed ONLY by <ThinkBlock> inside <StreamingMessage>.
 */
export const selectStreamingThink = (s: ChatStore): string => s.streamingThink;

// Collections — use with useShallow at call site (I3)
export const selectConversations = (s: ChatStore): ConversationSummary[] => s.conversations;
export const selectMessages = (s: ChatStore): ChatMessage[] => s.messages;

// Actions
export const selectSetConversations = (s: ChatStore): ChatActions["setConversations"] =>
  s.setConversations;
export const selectSetActiveConversationId = (
  s: ChatStore,
): ChatActions["setActiveConversationId"] => s.setActiveConversationId;
export const selectAddConversation = (s: ChatStore): ChatActions["addConversation"] =>
  s.addConversation;
export const selectRemoveConversation = (s: ChatStore): ChatActions["removeConversation"] =>
  s.removeConversation;
export const selectSetConversationsLoading = (
  s: ChatStore,
): ChatActions["setConversationsLoading"] => s.setConversationsLoading;
export const selectSetConversationsError = (
  s: ChatStore,
): ChatActions["setConversationsError"] => s.setConversationsError;
export const selectSetMessages = (s: ChatStore): ChatActions["setMessages"] => s.setMessages;
export const selectAppendMessage = (s: ChatStore): ChatActions["appendMessage"] =>
  s.appendMessage;
export const selectSetMessagesLoading = (s: ChatStore): ChatActions["setMessagesLoading"] =>
  s.setMessagesLoading;
export const selectSetMessagesError = (s: ChatStore): ChatActions["setMessagesError"] =>
  s.setMessagesError;
export const selectAppendToken = (s: ChatStore): ChatActions["appendToken"] => s.appendToken;
export const selectAppendThink = (s: ChatStore): ChatActions["appendThink"] => s.appendThink;
export const selectSetIsStreaming = (s: ChatStore): ChatActions["setIsStreaming"] =>
  s.setIsStreaming;
export const selectFinalizeTurn = (s: ChatStore): ChatActions["finalizeTurn"] => s.finalizeTurn;
export const selectClearStream = (s: ChatStore): ChatActions["clearStream"] => s.clearStream;

// ─── Shallow-equality hooks (I3) ─────────────────────────────────────────────

/** Hook: conversations list — shallow equality (I3). */
export function useConversations(): ConversationSummary[] {
  return useChatStore(useShallow(selectConversations));
}

/** Hook: settled messages — shallow equality (I3). */
export function useMessages(): ChatMessage[] {
  return useChatStore(useShallow(selectMessages));
}
