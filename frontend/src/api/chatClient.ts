/**
 * chatClient.ts — HTTP client for chat endpoints (ADR-0019 §2.2 / F6).
 *
 * INVARIANT I6: no provider_type / model_id sent from client.
 * INVARIANT I3: this module has zero parse logic — it only transports bytes.
 *
 * Endpoints:
 *   GET  /conversations
 *   POST /conversations
 *   GET  /conversations/{id}/messages
 *   DELETE /conversations/{id}
 *   POST /chat/stream  (NDJSON ReadableStream — consumed by useChatStream)
 */

import type { ConversationSummary, ChatMessage } from "../store/chatStore";

const API_BASE = (import.meta.env["VITE_API_BASE"] as string | undefined) ?? "";

// ─── REST helpers ─────────────────────────────────────────────────────────────

export interface ConversationListResponse {
  items: ConversationSummary[];
  total: number;
}

export interface MessageListResponse {
  items: ChatMessage[];
}

export async function fetchConversations(
  params?: { vault_id?: string; limit?: number; offset?: number },
  signal?: AbortSignal,
): Promise<ConversationListResponse> {
  const qs = new URLSearchParams();
  if (params?.vault_id) qs.set("vault_id", params.vault_id);
  if (params?.limit !== undefined) qs.set("limit", String(params.limit));
  if (params?.offset !== undefined) qs.set("offset", String(params.offset));
  const url = `${API_BASE}/conversations${qs.toString() ? "?" + qs.toString() : ""}`;
  const res = await fetch(url, { signal: signal ?? null });
  if (!res.ok) throw new Error(`GET /conversations: ${res.status}`);
  return res.json() as Promise<ConversationListResponse>;
}

export async function createConversation(
  body: { vault_id: string; title?: string },
  signal?: AbortSignal,
): Promise<ConversationSummary> {
  const res = await fetch(`${API_BASE}/conversations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: signal ?? null,
  });
  if (!res.ok) throw new Error(`POST /conversations: ${res.status}`);
  return res.json() as Promise<ConversationSummary>;
}

export async function fetchMessages(
  conversationId: string,
  signal?: AbortSignal,
): Promise<MessageListResponse> {
  const res = await fetch(`${API_BASE}/conversations/${conversationId}/messages`, {
    signal: signal ?? null,
  });
  if (!res.ok) throw new Error(`GET /conversations/${conversationId}/messages: ${res.status}`);
  return res.json() as Promise<MessageListResponse>;
}

export async function deleteConversation(
  conversationId: string,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${API_BASE}/conversations/${conversationId}`, {
    method: "DELETE",
    signal: signal ?? null,
  });
  if (!res.ok && res.status !== 204) throw new Error(`DELETE /conversations: ${res.status}`);
}

// ─── POST /chat/stream request shape (ADR-0019 §2.2) ─────────────────────────

export interface ChatMessageIn {
  role: "user" | "assistant" | "system";
  content: string;
}

/**
 * ChatStreamRequest — body for POST /chat/stream.
 *
 * INVARIANT I6: provider_type / model_id are NOT included — backend resolves them.
 */
export interface ChatStreamRequest {
  conversation_id: string | null;
  messages: ChatMessageIn[];
  vault_id?: string | null;
  context_window?: number | null;
  operation: "chat";
  regenerate?: boolean;
}

// ─── NDJSON event types (ADR-0019 §2.2 frozen schema) ────────────────────────

export interface TokenEvent {
  type: "token";
  delta: string;
}

export interface ThinkEvent {
  type: "think";
  delta: string;
}

export interface DoneEvent {
  type: "done";
  conversation_id: string;
  message_id: string;
  input_tokens: number;
  output_tokens: number;
  total_cost_usd: number;
  iterations_used: number;
  finish_reason: "stop" | "length" | "timeout";
}

export interface ErrorEvent {
  type: "error";
  code: "provider_timeout" | "provider_error" | "no_provider" | "budget_exceeded";
  message: string;
  total_cost_usd: number;
}

export type StreamEvent = TokenEvent | ThinkEvent | DoneEvent | ErrorEvent;

/**
 * openChatStream — returns a raw Response from POST /chat/stream.
 * The caller (useChatStream) reads response.body as an NDJSON ReadableStream.
 *
 * Does NOT parse events — transport only (I3).
 */
export async function openChatStream(
  body: ChatStreamRequest,
  signal: AbortSignal,
): Promise<Response> {
  const res = await fetch(`${API_BASE}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) {
    throw new Error(`POST /chat/stream: ${res.status}`);
  }
  return res;
}
