/**
 * chatStore-f5-select-preserve.test.ts — F5 fix: stale activeId in loadConversations.
 *
 * The bug: loadConversations had deps=[vaultId], so `activeId` captured in the closure
 * was stale (null from initial render). When the UXB-1 refresh effect called
 * loadConversations() after a completed turn, the stale `null` made
 * `if (!activeId && items.length > 0)` evaluate as true → selection jumped to items[0].
 *
 * The fix: read activeConversationId from useChatStore.getState() at execution time,
 * not from the closure.
 *
 * These tests verify the store contract the fix relies on:
 *   1. getState().activeConversationId reflects the current store value immediately.
 *   2. When activeConversationId is already set, the "auto-select first item" path
 *      is NOT taken (selection is preserved).
 *   3. When activeConversationId is null (initial load), the first item is selected.
 */

import { describe, it, expect, beforeEach } from "vitest";
import { useChatStore } from "../store/chatStore";
import type { ConversationSummary } from "../store/chatStore";

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

const ITEMS: ConversationSummary[] = [
  {
    id: "conv-first",
    vault_id: "v1",
    title: "First conv",
    created_at: "2026-01-01",
    updated_at: "2026-01-01",
  },
  {
    id: "conv-second",
    vault_id: "v1",
    title: "Second conv",
    created_at: "2026-01-02",
    updated_at: "2026-01-02",
  },
];

beforeEach(() => {
  resetStore();
});

// ─── F5-1: getState() reflects runtime value (not stale closure) ──────────────

describe("F5 — useChatStore.getState() always reflects current state", () => {
  it("getState().activeConversationId returns the current value after setActiveConversationId", () => {
    // Simulate: closure was created when activeId was null, then activeId was set
    const closureSnapshot = useChatStore.getState().activeConversationId; // null
    useChatStore.getState().setActiveConversationId("conv-X");

    const runtimeValue = useChatStore.getState().activeConversationId; // "conv-X"

    expect(closureSnapshot).toBeNull(); // stale closure would have captured null
    expect(runtimeValue).toBe("conv-X"); // getState() returns the live value
  });
});

// ─── F5-2: selection preserved after refresh (simulated fixed loadConversations) ─

describe("F5 — selection preserved when activeId is already set (simulated refresh)", () => {
  it("does NOT change activeConversationId when already set", () => {
    // Pre-condition: user has an active conversation (set after initial load)
    useChatStore.getState().setActiveConversationId("conv-existing");

    // Simulate what the FIXED loadConversations does on a UXB-1 refresh:
    //   - Fetches a fresh list
    //   - Reads activeId from getState() at execution time (NOT from closure)
    //   - Only auto-selects if there is no current activeId
    useChatStore.getState().setConversations(ITEMS);
    const currentActiveId = useChatStore.getState().activeConversationId; // "conv-existing"
    if (!currentActiveId && ITEMS.length > 0) {
      useChatStore.getState().setActiveConversationId(ITEMS[0]!.id);
    }

    // Selection must NOT have changed to "conv-first"
    expect(useChatStore.getState().activeConversationId).toBe("conv-existing");
  });

  it("does NOT change activeConversationId when it matches a list item", () => {
    // More realistic: user is on conv-second; refresh returns a list with conv-second
    useChatStore.getState().setActiveConversationId("conv-second");
    useChatStore.getState().setConversations(ITEMS);

    const currentActiveId = useChatStore.getState().activeConversationId;
    if (!currentActiveId && ITEMS.length > 0) {
      useChatStore.getState().setActiveConversationId(ITEMS[0]!.id);
    }

    expect(useChatStore.getState().activeConversationId).toBe("conv-second");
  });
});

// ─── F5-3: initial load auto-selects first item ───────────────────────────────

describe("F5 — initial load (no activeId) auto-selects first conversation", () => {
  it("sets activeConversationId to items[0].id when activeId is null", () => {
    // Pre-condition: no active conversation (fresh load)
    expect(useChatStore.getState().activeConversationId).toBeNull();

    useChatStore.getState().setConversations(ITEMS);
    const currentActiveId = useChatStore.getState().activeConversationId; // null
    if (!currentActiveId && ITEMS.length > 0) {
      useChatStore.getState().setActiveConversationId(ITEMS[0]!.id);
    }

    expect(useChatStore.getState().activeConversationId).toBe("conv-first");
  });

  it("leaves activeConversationId as null when items list is empty", () => {
    const currentActiveId = useChatStore.getState().activeConversationId; // null
    const emptyItems: ConversationSummary[] = [];
    if (!currentActiveId && emptyItems.length > 0) {
      useChatStore.getState().setActiveConversationId(emptyItems[0]!.id);
    }

    expect(useChatStore.getState().activeConversationId).toBeNull();
  });
});
