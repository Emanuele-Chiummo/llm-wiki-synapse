/**
 * buildMessagePayload.test.ts — unit tests for the chat message history helper.
 *
 * Covers AC-HARD-CONV-2 (architect C1 / DEFECT-M4H-007 / GAP-HARD-1).
 * N = message count (confirmed by i18n label: "{{count}} messages (about {{turns}} turns)").
 *
 * ACs verified:
 *   - fewer messages than N → returns all (no truncation)
 *   - exactly N messages → returns all
 *   - more than N messages → returns last N in original order
 *   - N=2 boundary
 *   - N=20 boundary
 *   - order is preserved (oldest first within the slice)
 */

import { describe, it, expect } from "vitest";
import { buildMessagePayload } from "../components/chat/buildMessagePayload";
import type { SimpleMessage } from "../components/chat/buildMessagePayload";

// ─── Helpers ──────────────────────────────────────────────────────────────────

function makeMessages(count: number): SimpleMessage[] {
  return Array.from({ length: count }, (_, i) => ({
    role: (i % 2 === 0 ? "user" : "assistant") as "user" | "assistant",
    content: `message-${i + 1}`,
  }));
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe("buildMessagePayload — fewer messages than historyLength", () => {
  it("returns all messages when count < historyLength", () => {
    const messages = makeMessages(3);
    const result = buildMessagePayload(messages, 10);
    expect(result).toHaveLength(3);
    expect(result.map((m) => m.content)).toEqual(["message-1", "message-2", "message-3"]);
  });

  it("returns all messages when count = 0 and historyLength = 4", () => {
    const result = buildMessagePayload([], 4);
    expect(result).toHaveLength(0);
  });

  it("returns all messages when count = 1 and historyLength = 4", () => {
    const messages = makeMessages(1);
    const result = buildMessagePayload(messages, 4);
    expect(result).toHaveLength(1);
  });
});

describe("buildMessagePayload — exactly N messages", () => {
  it("returns all messages when count = historyLength = 4", () => {
    const messages = makeMessages(4);
    const result = buildMessagePayload(messages, 4);
    expect(result).toHaveLength(4);
    expect(result.map((m) => m.content)).toEqual([
      "message-1",
      "message-2",
      "message-3",
      "message-4",
    ]);
  });

  it("returns all messages when count = historyLength = 10", () => {
    const messages = makeMessages(10);
    const result = buildMessagePayload(messages, 10);
    expect(result).toHaveLength(10);
  });
});

describe("buildMessagePayload — more than N messages (truncation)", () => {
  it("returns last N messages when count > historyLength", () => {
    const messages = makeMessages(10);
    const result = buildMessagePayload(messages, 4);
    expect(result).toHaveLength(4);
    // Last 4 of 10: messages 7, 8, 9, 10
    expect(result.map((m) => m.content)).toEqual([
      "message-7",
      "message-8",
      "message-9",
      "message-10",
    ]);
  });

  it("preserves original order within the slice (oldest first)", () => {
    const messages = makeMessages(6);
    const result = buildMessagePayload(messages, 3);
    expect(result).toHaveLength(3);
    // Last 3 of 6: messages 4, 5, 6 — in original chronological order
    expect(result[0]?.content).toBe("message-4");
    expect(result[1]?.content).toBe("message-5");
    expect(result[2]?.content).toBe("message-6");
  });

  it("roles are preserved correctly in the slice", () => {
    const messages = makeMessages(6);
    const result = buildMessagePayload(messages, 3);
    // makeMessages: index 3 → role "user" (3%2=1 → "assistant"), 4 → "user", 5 → "assistant"
    expect(result[0]?.role).toBe("assistant"); // message-4 = index 3
    expect(result[1]?.role).toBe("user"); // message-5 = index 4
    expect(result[2]?.role).toBe("assistant"); // message-6 = index 5
  });
});

describe("buildMessagePayload — N=2 boundary (smallest valid CONV_HISTORY_OPTION)", () => {
  it("N=2: returns last 2 from 10 messages", () => {
    const messages = makeMessages(10);
    const result = buildMessagePayload(messages, 2);
    expect(result).toHaveLength(2);
    expect(result.map((m) => m.content)).toEqual(["message-9", "message-10"]);
  });

  it("N=2: returns all when only 1 message exists", () => {
    const messages = makeMessages(1);
    const result = buildMessagePayload(messages, 2);
    expect(result).toHaveLength(1);
  });

  it("N=2: returns all when exactly 2 messages exist", () => {
    const messages = makeMessages(2);
    const result = buildMessagePayload(messages, 2);
    expect(result).toHaveLength(2);
  });
});

describe("buildMessagePayload — N=20 boundary (largest valid CONV_HISTORY_OPTION)", () => {
  it("N=20: returns all when count < 20", () => {
    const messages = makeMessages(15);
    const result = buildMessagePayload(messages, 20);
    expect(result).toHaveLength(15);
  });

  it("N=20: returns all when count = 20", () => {
    const messages = makeMessages(20);
    const result = buildMessagePayload(messages, 20);
    expect(result).toHaveLength(20);
  });

  it("N=20: returns last 20 when count = 25", () => {
    const messages = makeMessages(25);
    const result = buildMessagePayload(messages, 20);
    expect(result).toHaveLength(20);
    expect(result[0]?.content).toBe("message-6");
    expect(result[19]?.content).toBe("message-25");
  });
});

describe("buildMessagePayload — edge cases", () => {
  it("historyLength=0 returns empty array", () => {
    const messages = makeMessages(5);
    const result = buildMessagePayload(messages, 0);
    expect(result).toHaveLength(0);
  });

  it("does not mutate the original array", () => {
    const messages = makeMessages(5);
    const original = messages.map((m) => ({ ...m }));
    buildMessagePayload(messages, 3);
    expect(messages).toEqual(original);
  });

  it("result is a new array (not the same reference)", () => {
    const messages = makeMessages(3);
    const result = buildMessagePayload(messages, 10);
    expect(result).not.toBe(messages);
  });
});
