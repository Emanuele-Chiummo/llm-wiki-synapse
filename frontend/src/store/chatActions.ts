/**
 * chatActions.ts — cross-cutting chat actions that are more than a single Zustand
 * setter (call the API, then update chatStore). Kept OUTSIDE chatStore.ts itself
 * (whose shape must match ADR-0019 §3 exactly) so multiple entry points — the
 * ConversationList "+" button and the Command Palette (v2, FE-UIUX-3) — share
 * one implementation instead of duplicating the create → addConversation →
 * setActiveConversationId → setMessages([]) sequence.
 */

import { createConversation } from "../api/chatClient";
import { useChatStore, type ConversationSummary } from "./chatStore";

/**
 * Create a new conversation for `vaultId` and make it the active one.
 * Throws on API failure — callers show their own toast/error handling.
 */
export async function startNewConversation(vaultId: string): Promise<ConversationSummary> {
  const conv = await createConversation({ vault_id: vaultId });
  const { addConversation, setActiveConversationId, setMessages } = useChatStore.getState();
  addConversation(conv);
  setActiveConversationId(conv.id);
  setMessages([]);
  return conv;
}
