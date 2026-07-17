/**
 * chat.ts — shared domain types for the Chat feature (F6 / ADR-0019 §2.2 / ADR-0022).
 *
 * Previously these types were split between chatClient.ts (WebCitationRef) and
 * chatStore.ts (ConversationSummary, CitationRef, ChatMessage, ...), which created
 * a circular import: chatClient → chatStore → chatClient. Both modules now import
 * from here instead; neither imports the other.
 *
 * Consumers may import from the original modules (chatStore.ts / chatClient.ts) as
 * before — both re-export everything they used to define — so no call site changes.
 */

// ─── Web-citation reference (from server SearXNG search, B2) ─────────────────

/**
 * WebCitationRef — a web page citation included in a done event (B2 — web search).
 * Cited in the response text as [W1], [W2], etc.
 * Distinct from wiki CitationRef (which uses [1], [2] etc.).
 */
export interface WebCitationRef {
  /** 1-based index matching [Wn] markers in the message content. */
  index: number;
  /** Page title from the web source. */
  title: string;
  /** Source URL — opens in new tab. */
  url: string;
}

// ─── Conversation / message domain types ─────────────────────────────────────

export interface ConversationSummary {
  id: string;
  vault_id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
  /**
   * UXB-1: preview snippet — first 80 chars of the last message, server-generated.
   * null when no messages exist yet (new conversation).
   * Non-breaking additive field; older servers omit it.
   */
  preview?: string | null;
}

export type ChatRole = "user" | "assistant" | "system";

/**
 * CitationRef — compact citation reference carried from the done event (ADR-0022 §2.4).
 * Shape: { n, id, title, slug } — score/phase are stored server-side, not streamed.
 */
export interface CitationRef {
  /** 1-based citation index matching [n] markers in the message content. */
  n: number;
  /** UUID of the pages row (source document). */
  id: string;
  /** Display title of the source page (never empty). */
  title: string;
  /** URL-friendly slug derived from the title. */
  slug: string;
}

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
  /**
   * Citations carried from the done event (ADR-0022 §2.4).
   * Empty array when retrieval produced no citations (non-breaking additive field).
   */
  citations: CitationRef[];
  /**
   * Web citations from a SearXNG search (B2).
   * Present when use_web_search=true. Empty array when not set. Non-breaking additive field.
   */
  web_citations?: WebCitationRef[];
}

export interface LastUsage {
  inputTokens: number;
  outputTokens: number;
  totalCostUsd: number;
}
