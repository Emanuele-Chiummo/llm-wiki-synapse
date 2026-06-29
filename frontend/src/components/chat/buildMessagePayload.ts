/**
 * buildMessagePayload.ts — pure helper for assembling the messages array sent to the
 * backend on each chat request (AC-HARD-CONV-2 / architect C1 / I7).
 *
 * N = message count (not turns). The i18n label confirms this:
 *   "{{count}} messages (about {{turns}} turns)" where turns = count / 2.
 *
 * Usage (new send path):
 *   const history = buildMessagePayload(messages, historyLength);
 *   // Append the new user message, then send.
 *
 * Usage (regenerate path):
 *   const history = buildMessagePayload(messagesUpToUser, historyLength);
 *   // Send directly.
 *
 * INVARIANT I3: pure function, no store access, no side effects.
 * INVARIANT I7: historyLength is the context-budget enforcement mechanism.
 */

export interface SimpleMessage {
  role: "user" | "assistant" | "system";
  content: string;
}

/**
 * Slice messages to at most `historyLength` of the most-recent entries.
 *
 * @param messages  Full ordered list of messages (chronological, oldest first).
 * @param historyLength  Max messages to include. Must be >= 1.
 * @returns  At most the last `historyLength` messages, in original order.
 *
 * Boundary cases:
 *   - fewer messages than historyLength → returns all messages (no truncation)
 *   - exactly historyLength → returns all messages
 *   - more than historyLength → returns the last historyLength messages
 *   - historyLength = 2 → last 2 messages only
 *   - historyLength = 20 → last 20 messages (or fewer if history is shorter)
 */
export function buildMessagePayload(
  messages: ReadonlyArray<SimpleMessage>,
  historyLength: number,
): SimpleMessage[] {
  if (historyLength <= 0) return [];
  if (messages.length <= historyLength) return [...messages];
  return messages.slice(messages.length - historyLength) as SimpleMessage[];
}
