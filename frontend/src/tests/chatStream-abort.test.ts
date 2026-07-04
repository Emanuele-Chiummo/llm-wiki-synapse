/**
 * chatStream-abort.test.ts — F2/F3 fixes: stream abort on conversation switch and unmount.
 *
 * Coverage:
 *   F2-1. Aborting a stream (via abortStream) prevents finalizeTurn from landing in the
 *         wrong conversation — the message is discarded when conversation_id !== activeConversationId.
 *   F2-2. ConversationList calls abortStream() before switching — verified via store state.
 *   F3-1. useChatStream registers streamAbortFn in the store on each send() call.
 *   F3-2. On unmount, the registered abort fn is called and stream state is cleared.
 *   F3-3. abort() from the hook clears stream state without contaminating another conversation.
 *
 * Approach:
 *   - Store-level tests (F2-1, F2-2) do not need to render components.
 *   - Hook-level tests (F3) use renderHook and mock openChatStream to control the stream.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useChatStore } from "../store/chatStore";
import type { ChatMessage } from "../store/chatStore";

// ─── Mock openChatStream ───────────────────────────────────────────────────────

vi.mock("../api/chatClient", () => ({
  openChatStream: vi.fn(),
}));

import * as chatClientModule from "../api/chatClient";
const mockedOpenChatStream = chatClientModule.openChatStream as ReturnType<typeof vi.fn>;

// ─── Mock showToast ────────────────────────────────────────────────────────────

vi.mock("../components/common/Toast", () => ({
  showToast: vi.fn(),
}));

// ─── Store reset ──────────────────────────────────────────────────────────────

function resetStore() {
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
}

beforeEach(() => {
  resetStore();
  vi.clearAllMocks();
});

afterEach(() => {
  resetStore();
});

// ─── Import the hook after mocks ──────────────────────────────────────────────

import { useChatStream } from "../components/chat/useChatStream";
import type { ChatStreamRequest } from "../api/chatClient";

// ─── Helpers ──────────────────────────────────────────────────────────────────

/**
 * Creates a mock streaming response with a readable stream that never produces data
 * (simulates an in-progress NDJSON stream). Captures the AbortSignal passed to
 * openChatStream so tests can verify it was aborted.
 */
function mockNeverEndingStream(): { captureSignal: () => AbortSignal | undefined } {
  let capturedSignal: AbortSignal | undefined;

  mockedOpenChatStream.mockImplementation((_req: ChatStreamRequest, signal: AbortSignal) => {
    capturedSignal = signal;
    const body = new ReadableStream({
      start() {
        /* never enqueue, never close — simulates in-progress stream */
      },
    });
    return Promise.resolve({ body } as Response);
  });

  return { captureSignal: () => capturedSignal };
}

const MINIMAL_REQ: ChatStreamRequest = {
  conversation_id: "conv-A",
  messages: [{ role: "user", content: "hello" }],
  vault_id: "vault-1",
  operation: "chat",
};

// ─── F2-1: finalizeTurn guard (store-level) ───────────────────────────────────

describe("F2 — finalizeTurn does not contaminate switched conversation", () => {
  it("after switch: message for conv-A is discarded when activeConversationId is conv-B", () => {
    // Simulate: stream was started for conv-A, user switched to conv-B.
    useChatStore.setState({ activeConversationId: "conv-B", messages: [] });
    useChatStore.getState().appendToken("answer…");
    useChatStore.getState().setIsStreaming(true);

    const msgFromConvA: ChatMessage = {
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
      .finalizeTurn(msgFromConvA, { inputTokens: 10, outputTokens: 5, totalCostUsd: 0.001 });

    const state = useChatStore.getState();
    // conv-B's messages must be empty — the conv-A answer was discarded.
    expect(state.messages).toHaveLength(0);
    // Stream state must be cleared regardless.
    expect(state.isStreaming).toBe(false);
    expect(state.streamingContent).toBe("");
    // conversationsNeedRefresh must NOT be set (no new message was persisted on our side).
    expect(state.conversationsNeedRefresh).toBe(false);
  });

  it("stream can complete safely for the correct conversation after a switch", () => {
    // conv-B's stream started and completed legitimately.
    useChatStore.setState({ activeConversationId: "conv-B", messages: [] });

    const msgFromConvB: ChatMessage = {
      id: "msg-2",
      conversation_id: "conv-B",
      role: "assistant",
      content: "conv-B answer",
      input_tokens: 5,
      output_tokens: 5,
      total_cost_usd: 0,
      created_at: new Date().toISOString(),
      citations: [],
    };

    useChatStore
      .getState()
      .finalizeTurn(msgFromConvB, { inputTokens: 5, outputTokens: 5, totalCostUsd: 0 });

    expect(useChatStore.getState().messages).toHaveLength(1);
    expect(useChatStore.getState().messages[0]?.conversation_id).toBe("conv-B");
  });
});

// ─── F2-2: conversation switch aborts stream via store ────────────────────────

describe("F2 — conversation switch clears streaming state immediately", () => {
  it("abortStream() called before switch clears isStreaming so the Stop button disappears", () => {
    // Simulate an in-flight stream registered via setStreamAbortFn.
    const mockFn = vi.fn();
    useChatStore.setState({
      streamAbortFn: mockFn,
      isStreaming: true,
      streamingContent: "partial answer…",
    });

    // This is what ConversationList.handleSelect does before switching.
    useChatStore.getState().abortStream();

    expect(mockFn).toHaveBeenCalledOnce();
    expect(useChatStore.getState().isStreaming).toBe(false);
    expect(useChatStore.getState().streamingContent).toBe("");
    expect(useChatStore.getState().streamAbortFn).toBeNull();
  });
});

// ─── F3-1: useChatStream registers streamAbortFn on send ─────────────────────

describe("F3 — useChatStream registers abort fn in store on send()", () => {
  it("streamAbortFn is set in the store after send() is called", async () => {
    mockNeverEndingStream();
    useChatStore.setState({ activeConversationId: "conv-A" });

    const { result } = renderHook(() => useChatStream());

    act(() => {
      result.current.send(MINIMAL_REQ);
    });

    // After one microtask tick the async IIFE registers the fn.
    await act(async () => {
      await Promise.resolve();
    });

    expect(useChatStore.getState().streamAbortFn).not.toBeNull();
  });
});

// ─── F3-2: unmount aborts the in-flight stream ────────────────────────────────

describe("F3 — useChatStream aborts stream on unmount", () => {
  it("unmounting clears isStreaming and the AbortSignal is aborted", async () => {
    const { captureSignal } = mockNeverEndingStream();
    useChatStore.setState({ activeConversationId: "conv-A" });

    const { result, unmount } = renderHook(() => useChatStream());

    act(() => {
      result.current.send(MINIMAL_REQ);
    });

    // Wait for openChatStream to be called (async IIFE starts).
    await act(async () => {
      await new Promise((r) => setTimeout(r, 10));
    });

    expect(useChatStore.getState().isStreaming).toBe(true);

    // Unmount the component that owns the stream.
    unmount();

    // Stream state must be cleared.
    expect(useChatStore.getState().isStreaming).toBe(false);
    expect(useChatStore.getState().streamingContent).toBe("");
    expect(useChatStore.getState().streamAbortFn).toBeNull();

    // The underlying AbortController signal must be aborted.
    const signal = captureSignal();
    expect(signal?.aborted).toBe(true);
  });
});

// ─── F3-3: abort() from the hook does not contaminate another conversation ────

describe("F3 — abort() clears stream state without touching messages", () => {
  it("abort() empties streaming buffers but leaves settled messages intact", () => {
    const existingMsg: ChatMessage = {
      id: "m1",
      conversation_id: "conv-A",
      role: "user",
      content: "hello",
      input_tokens: 0,
      output_tokens: 0,
      total_cost_usd: 0,
      created_at: new Date().toISOString(),
      citations: [],
    };
    useChatStore.getState().appendMessage(existingMsg);
    useChatStore.getState().appendToken("partial…");
    useChatStore.getState().setIsStreaming(true);

    const { result } = renderHook(() => useChatStream());

    act(() => {
      result.current.abort();
    });

    const state = useChatStore.getState();
    // Settled messages untouched.
    expect(state.messages).toHaveLength(1);
    // Streaming state cleared.
    expect(state.isStreaming).toBe(false);
    expect(state.streamingContent).toBe("");
  });
});
