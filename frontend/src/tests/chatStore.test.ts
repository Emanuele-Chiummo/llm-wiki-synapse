/**
 * chatStore.test.ts — unit tests for chatStore (ADR-0019 §3 / I3 / G3).
 *
 * Tests:
 *   - Store shape: initial state correct.
 *   - appendToken / appendThink: cheap string appends, no other state change.
 *   - finalizeTurn: moves buffer to messages, clears stream state.
 *   - clearStream: clears buffers without touching messages.
 *   - selectStreamingContent / selectStreamingThink: only those fields changed by appends.
 *   - AC-G3-4: settled messages array does NOT update when token is appended.
 *   - AC-G3-3: 100 appendToken calls produce exactly 0 re-subscriptions from the messages
 *     selector (verified by checking messages.length stays stable).
 */

import { describe, it, expect, beforeEach, vi } from "vitest";
import { useChatStore } from "../store/chatStore";
import type { ChatMessage } from "../store/chatStore";

// Reset store state between tests
beforeEach(() => {
  useChatStore.setState({
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
    conversationsNeedRefresh: false,
    streamAbortFn: null,
  });
});

describe("chatStore — initial state", () => {
  it("starts with empty streaming buffers", () => {
    const { streamingContent, streamingThink } = useChatStore.getState();
    expect(streamingContent).toBe("");
    expect(streamingThink).toBe("");
  });

  it("starts with isStreaming=false", () => {
    expect(useChatStore.getState().isStreaming).toBe(false);
  });

  it("starts with empty messages array", () => {
    expect(useChatStore.getState().messages).toHaveLength(0);
  });
});

describe("chatStore — appendToken (AC-G3-4)", () => {
  it("appends delta to streamingContent", () => {
    const { appendToken } = useChatStore.getState();
    appendToken("Hello");
    appendToken(", ");
    appendToken("world");
    expect(useChatStore.getState().streamingContent).toBe("Hello, world");
  });

  it("does NOT change the messages array when a token is appended", () => {
    const before = useChatStore.getState().messages;
    useChatStore.getState().appendToken("some text");
    const after = useChatStore.getState().messages;
    // Same reference (no new array allocated if messages was not changed)
    expect(after).toBe(before);
  });

  it("does NOT change isStreaming when a token is appended", () => {
    useChatStore.getState().appendToken("x");
    expect(useChatStore.getState().isStreaming).toBe(false);
  });
});

describe("chatStore — appendThink", () => {
  it("appends delta to streamingThink", () => {
    const { appendThink } = useChatStore.getState();
    appendThink("let me ");
    appendThink("think");
    expect(useChatStore.getState().streamingThink).toBe("let me think");
  });

  it("does NOT affect streamingContent", () => {
    useChatStore.getState().appendToken("visible");
    useChatStore.getState().appendThink("hidden");
    expect(useChatStore.getState().streamingContent).toBe("visible");
  });
});

describe("chatStore — AC-G3-3: zero selector recomputes during stream", () => {
  it("100 appendToken calls: messages.length stays 0 (settled array not updated per token)", () => {
    useChatStore.getState().setIsStreaming(true);

    // Spy on how many times the messages array changes by comparing references
    let messageArrayChangeCount = 0;
    let prevMessages = useChatStore.getState().messages;

    const unsubscribe = useChatStore.subscribe((state) => {
      if (state.messages !== prevMessages) {
        messageArrayChangeCount++;
        prevMessages = state.messages;
      }
    });

    for (let i = 0; i < 100; i++) {
      useChatStore.getState().appendToken(`token${i}`);
    }

    unsubscribe();

    // The messages array must NOT have been replaced during streaming tokens (AC-G3-4)
    expect(messageArrayChangeCount).toBe(0);
    // streamingContent should have all 100 appended tokens
    expect(useChatStore.getState().streamingContent).toContain("token0");
    expect(useChatStore.getState().streamingContent).toContain("token99");
  });
});

describe("chatStore — finalizeTurn", () => {
  it("appends message to messages array", () => {
    const msg: ChatMessage = {
      id: "msg-1",
      conversation_id: "conv-1",
      role: "assistant",
      content: "Hello world",
      input_tokens: 10,
      output_tokens: 5,
      total_cost_usd: 0.0001,
      created_at: new Date().toISOString(),
      citations: [],
    };
    const usage = { inputTokens: 10, outputTokens: 5, totalCostUsd: 0.0001 };

    useChatStore.getState().appendToken("Hello world");
    useChatStore.getState().setIsStreaming(true);
    useChatStore.getState().finalizeTurn(msg, usage);

    const state = useChatStore.getState();
    expect(state.messages).toHaveLength(1);
    expect(state.messages[0]?.content).toBe("Hello world");
    expect(state.streamingContent).toBe("");
    expect(state.streamingThink).toBe("");
    expect(state.isStreaming).toBe(false);
    expect(state.lastUsage?.totalCostUsd).toBe(0.0001);
  });

  it("sets lastUsage from the done event (I7)", () => {
    const msg: ChatMessage = {
      id: "msg-2",
      conversation_id: "conv-1",
      role: "assistant",
      content: "test",
      input_tokens: 100,
      output_tokens: 50,
      total_cost_usd: 0.0042,
      created_at: new Date().toISOString(),
      citations: [],
    };
    useChatStore.getState().finalizeTurn(msg, {
      inputTokens: 100,
      outputTokens: 50,
      totalCostUsd: 0.0042,
    });
    const { lastUsage } = useChatStore.getState();
    expect(lastUsage?.inputTokens).toBe(100);
    expect(lastUsage?.outputTokens).toBe(50);
    expect(lastUsage?.totalCostUsd).toBe(0.0042);
  });
});

describe("chatStore — clearStream", () => {
  it("clears streaming buffers but leaves messages intact", () => {
    // Put a settled message in
    const msg: ChatMessage = {
      id: "m1",
      conversation_id: "c1",
      role: "user",
      content: "hi",
      input_tokens: 1,
      output_tokens: 0,
      total_cost_usd: 0,
      created_at: new Date().toISOString(),
      citations: [],
    };
    useChatStore.getState().appendMessage(msg);
    useChatStore.getState().appendToken("streaming...");
    useChatStore.getState().setIsStreaming(true);

    useChatStore.getState().clearStream();

    const state = useChatStore.getState();
    expect(state.streamingContent).toBe("");
    expect(state.streamingThink).toBe("");
    expect(state.isStreaming).toBe(false);
    // Messages array unchanged
    expect(state.messages).toHaveLength(1);
  });
});

describe("chatStore — conversation management", () => {
  it("addConversation prepends to the list", () => {
    useChatStore
      .getState()
      .setConversations([
        { id: "c1", vault_id: "v", title: "First", created_at: "", updated_at: "" },
      ]);
    useChatStore.getState().addConversation({
      id: "c2",
      vault_id: "v",
      title: "Second",
      created_at: "",
      updated_at: "",
    });
    const { conversations } = useChatStore.getState();
    expect(conversations[0]?.id).toBe("c2");
    expect(conversations[1]?.id).toBe("c1");
  });

  it("removeConversation resets activeConversationId if it was the removed one", () => {
    useChatStore.setState({
      conversations: [{ id: "c1", vault_id: "v", title: null, created_at: "", updated_at: "" }],
      activeConversationId: "c1",
    });
    useChatStore.getState().removeConversation("c1");
    expect(useChatStore.getState().activeConversationId).toBeNull();
  });

  it("removeConversation keeps activeConversationId if different", () => {
    useChatStore.setState({
      conversations: [
        { id: "c1", vault_id: "v", title: null, created_at: "", updated_at: "" },
        { id: "c2", vault_id: "v", title: null, created_at: "", updated_at: "" },
      ],
      activeConversationId: "c2",
    });
    useChatStore.getState().removeConversation("c1");
    expect(useChatStore.getState().activeConversationId).toBe("c2");
  });
});

describe("chatStore — selectStreamingContent isolation (AC-G3-4)", () => {
  it("streamingContent selector returns only the streaming buffer", () => {
    const { appendToken } = useChatStore.getState();
    appendToken("partial");
    const content = useChatStore.getState().streamingContent;
    expect(content).toBe("partial");
  });

  it("spy: no call to messages selector when only tokens appended", () => {
    // Simulate what a component subscribing to messages would observe.
    // We use subscribe() to track changes to the messages field.
    const messagesCalls: number[] = [];
    const unsub = useChatStore.subscribe((s) => {
      // The subscriber fires on ANY state change; we track if messages actually changed
      messagesCalls.push(s.messages.length);
    });

    // Simulate streaming — 50 token appends
    for (let i = 0; i < 50; i++) {
      useChatStore.getState().appendToken("a");
    }

    unsub();

    // All 50 subscriber calls should have seen messages.length = 0
    // (messages array identity never changed during pure token streaming)
    expect(messagesCalls.every((n) => n === 0)).toBe(true);
  });
});

describe("chatStore — setIsStreaming", () => {
  it("sets the streaming flag", () => {
    useChatStore.getState().setIsStreaming(true);
    expect(useChatStore.getState().isStreaming).toBe(true);
    useChatStore.getState().setIsStreaming(false);
    expect(useChatStore.getState().isStreaming).toBe(false);
  });
});

describe("chatStore — setStreamError", () => {
  it("sets and clears error", () => {
    useChatStore.getState().setStreamError("timeout");
    expect(useChatStore.getState().streamError).toBe("timeout");
    useChatStore.getState().setStreamError(null);
    expect(useChatStore.getState().streamError).toBeNull();
  });
});

// ─── F2: finalizeTurn conversation-id guard ───────────────────────────────────

describe("chatStore — F2: finalizeTurn guards by conversation_id", () => {
  it("discards the message and clears stream state when msg.conversation_id !== activeConversationId", () => {
    // User started a stream for conv-A, then switched to conv-B.
    useChatStore.setState({ activeConversationId: "conv-B", messages: [] });
    useChatStore.getState().appendToken("answer for conv-A...");
    useChatStore.getState().setIsStreaming(true);

    const msgForA: ChatMessage = {
      id: "msg-1",
      conversation_id: "conv-A", // does NOT match activeConversationId
      role: "assistant",
      content: "answer for conv-A",
      input_tokens: 10,
      output_tokens: 5,
      total_cost_usd: 0.001,
      created_at: new Date().toISOString(),
      citations: [],
    };

    useChatStore
      .getState()
      .finalizeTurn(msgForA, { inputTokens: 10, outputTokens: 5, totalCostUsd: 0.001 });

    const state = useChatStore.getState();
    // Message must NOT appear in conv-B's messages list.
    expect(state.messages).toHaveLength(0);
    // Stream state must be cleared.
    expect(state.isStreaming).toBe(false);
    expect(state.streamingContent).toBe("");
    expect(state.streamingThink).toBe("");
    // streamAbortFn cleared.
    expect(state.streamAbortFn).toBeNull();
    // lastUsage is still updated (billing info).
    expect(state.lastUsage?.totalCostUsd).toBe(0.001);
    // conversationsNeedRefresh must NOT be set for the discarded message.
    expect(state.conversationsNeedRefresh).toBe(false);
  });

  it("appends the message normally when conversation_id matches activeConversationId", () => {
    useChatStore.setState({ activeConversationId: "conv-A", messages: [] });
    useChatStore.getState().appendToken("answer");
    useChatStore.getState().setIsStreaming(true);

    const msgForA: ChatMessage = {
      id: "msg-1",
      conversation_id: "conv-A",
      role: "assistant",
      content: "answer",
      input_tokens: 10,
      output_tokens: 5,
      total_cost_usd: 0.001,
      created_at: new Date().toISOString(),
      citations: [],
    };

    useChatStore
      .getState()
      .finalizeTurn(msgForA, { inputTokens: 10, outputTokens: 5, totalCostUsd: 0.001 });

    const state = useChatStore.getState();
    expect(state.messages).toHaveLength(1);
    expect(state.messages[0]?.content).toBe("answer");
    expect(state.isStreaming).toBe(false);
    expect(state.conversationsNeedRefresh).toBe(true);
  });

  it("appends normally when activeConversationId is null (first message in a new conversation)", () => {
    // activeConversationId starts null (e.g., brand-new conversation)
    useChatStore.setState({ activeConversationId: null, messages: [] });

    const msg: ChatMessage = {
      id: "msg-1",
      conversation_id: "conv-new",
      role: "assistant",
      content: "hello",
      input_tokens: 5,
      output_tokens: 5,
      total_cost_usd: 0,
      created_at: new Date().toISOString(),
      citations: [],
    };

    useChatStore.getState().finalizeTurn(msg, { inputTokens: 5, outputTokens: 5, totalCostUsd: 0 });

    const state = useChatStore.getState();
    expect(state.messages).toHaveLength(1);
    // activeConversationId is adopted from the message.
    expect(state.activeConversationId).toBe("conv-new");
  });
});

// ─── F2/F3: abortStream action ────────────────────────────────────────────────

describe("chatStore — F2/F3: abortStream action", () => {
  it("calls the registered streamAbortFn and clears stream state", () => {
    const mockAbort = vi.fn();
    useChatStore.setState({
      streamAbortFn: mockAbort,
      isStreaming: true,
      streamingContent: "partial",
      streamingThink: "thinking",
    });

    useChatStore.getState().abortStream();

    expect(mockAbort).toHaveBeenCalledOnce();
    const state = useChatStore.getState();
    expect(state.isStreaming).toBe(false);
    expect(state.streamingContent).toBe("");
    expect(state.streamingThink).toBe("");
    expect(state.streamAbortFn).toBeNull();
  });

  it("is a no-op when streamAbortFn is null (no in-flight stream)", () => {
    useChatStore.setState({ streamAbortFn: null, isStreaming: false });
    // Should not throw.
    expect(() => useChatStore.getState().abortStream()).not.toThrow();
  });

  it("does not call the old fn a second time if abortStream is called twice", () => {
    const mockAbort = vi.fn();
    useChatStore.setState({ streamAbortFn: mockAbort, isStreaming: true });

    useChatStore.getState().abortStream();
    useChatStore.getState().abortStream(); // second call — fn is already null

    expect(mockAbort).toHaveBeenCalledTimes(1);
  });
});

// ─── F2/F3: setStreamAbortFn action ──────────────────────────────────────────

describe("chatStore — F2/F3: setStreamAbortFn", () => {
  it("stores the provided function", () => {
    const fn = vi.fn();
    useChatStore.getState().setStreamAbortFn(fn);
    expect(useChatStore.getState().streamAbortFn).toBe(fn);
  });

  it("can be cleared to null", () => {
    useChatStore.getState().setStreamAbortFn(vi.fn());
    useChatStore.getState().setStreamAbortFn(null);
    expect(useChatStore.getState().streamAbortFn).toBeNull();
  });
});

// Silence unused-import lint — vi is used implicitly via vitest globals
void vi;
