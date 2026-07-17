/**
 * ConversationListRename.test.tsx — vitest tests for R7-3 rename + filter.
 *
 * Covers:
 *   - Filter input renders (AC-R7-3-3)
 *   - Pencil (rename) button visible on hover
 *   - Optimistic update: store updated before PATCH responds
 *   - On PATCH success: store updated from response
 *   - On PATCH error: rollback to original title
 *   - Filter hides non-matching conversations (AC-R7-3-3)
 *   - Empty filter state shows no-match message
 *   - Esc cancels rename without saving
 *
 * INVARIANT I3: Zustand shallow selectors, no heavy subscriptions.
 * INVARIANT I4: TanStack Virtual mocked.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ConversationList } from "../components/chat/ConversationList";

// ─── Mock TanStack Virtual ────────────────────────────────────────────────────

vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: (opts: { count: number; estimateSize: (i: number) => number }) => ({
    getVirtualItems: () =>
      Array.from({ length: opts.count }, (_, i) => ({
        index: i,
        start: i * opts.estimateSize(i),
        end: (i + 1) * opts.estimateSize(i),
        size: opts.estimateSize(i),
        key: i,
        lane: 0,
      })),
    getTotalSize: () => opts.count * 36,
    measureElement: () => undefined,
  }),
}));

// ─── Mock chatClient ──────────────────────────────────────────────────────────

vi.mock("../api/chatClient", () => ({
  renameConversation: vi.fn(),
  fetchConversations: vi.fn().mockResolvedValue([]),
  createConversation: vi.fn(),
  deleteConversation: vi.fn(),
  fetchMessages: vi.fn().mockResolvedValue([]),
}));

import * as chatClient from "../api/chatClient";

// ─── Mock graphStore ──────────────────────────────────────────────────────────

vi.mock("../store/graphStore", () => ({
  useGraphStore: (selector: (s: unknown) => unknown) => selector({ vaultId: "v1" }),
  selectVaultId: (s: { vaultId: string }) => s.vaultId,
}));

// ─── Mock chatStore ───────────────────────────────────────────────────────────

const mockUpdateConversation = vi.fn();

vi.mock("../store/chatStore", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../store/chatStore")>();
  return {
    ...actual,
    useChatStore: vi.fn(),
    useConversations: vi.fn(),
  };
});

import { useChatStore, useConversations } from "../store/chatStore";

const SAMPLE_CONVERSATIONS = [
  {
    id: "c1",
    vault_id: "v1",
    title: "Alpha conversation",
    created_at: "2026-01-01",
    updated_at: "2026-01-01",
  },
  {
    id: "c2",
    vault_id: "v1",
    title: "Beta conversation",
    created_at: "2026-01-02",
    updated_at: "2026-01-02",
  },
];

// ─── Mock i18n ────────────────────────────────────────────────────────────────

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const map: Record<string, string> = {
        "chat.conversations": "Conversations",
        "chat.newConversation": "New conversation",
        "chat.noConversations": "No conversations yet. Click + to start.",
        "chat.untitled": "Untitled",
        "chat.deleteConversation": "Delete conversation",
        "chat.searchConversations": "Search conversations…",
        "chat.noMatchingConversations": "No conversations match your search.",
        "chat.renameConversation": "Rename conversation",
        "chat.renameCommit": "Save rename",
        "chat.renameCancel": "Cancel rename",
        "chat.renameError": "Failed to rename conversation",
      };
      return map[key] ?? key;
    },
    i18n: { language: "en" },
  }),
}));

// ─── Mock Toast ───────────────────────────────────────────────────────────────

vi.mock("../components/common/Toast", () => ({
  showToast: vi.fn(),
}));

// ─── Setup ────────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();

  vi.mocked(useConversations).mockReturnValue(SAMPLE_CONVERSATIONS);

  const storeState = {
    conversations: SAMPLE_CONVERSATIONS,
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
    // UXB-1
    conversationsNeedRefresh: false,
    clearConversationsNeedRefresh: vi.fn(),
    setConversations: vi.fn(),
    setActiveConversationId: vi.fn(),
    addConversation: vi.fn(),
    removeConversation: vi.fn(),
    updateConversation: mockUpdateConversation,
    setConversationsLoading: vi.fn(),
    setConversationsError: vi.fn(),
    setMessages: vi.fn(),
    appendMessage: vi.fn(),
    setMessagesLoading: vi.fn(),
    setMessagesError: vi.fn(),
    appendToken: vi.fn(),
    appendThink: vi.fn(),
    setIsStreaming: vi.fn(),
    setStreamError: vi.fn(),
    finalizeTurn: vi.fn(),
    updateMessageCitations: vi.fn(),
    clearStream: vi.fn(),
  };

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  vi.mocked(useChatStore).mockImplementation((selector: (s: any) => unknown) =>
    selector(storeState),
  );
});

// ─── Tests ────────────────────────────────────────────────────────────────────

describe("ConversationList — R7-3 filter (AC-R7-3-3)", () => {
  it("renders the filter input", async () => {
    render(<ConversationList />);
    await waitFor(() => {
      expect(screen.getByTestId("conversation-filter-input")).toBeTruthy();
    });
  });

  it("shows all conversations when filter is empty", async () => {
    render(<ConversationList />);
    await waitFor(() => {
      expect(screen.getByText("Alpha conversation")).toBeTruthy();
      expect(screen.getByText("Beta conversation")).toBeTruthy();
    });
  });

  it("filters conversations by typed text", async () => {
    render(<ConversationList />);
    await waitFor(() => {
      expect(screen.getByTestId("conversation-filter-input")).toBeTruthy();
    });

    fireEvent.change(screen.getByTestId("conversation-filter-input"), {
      target: { value: "Alpha" },
    });

    await waitFor(() => {
      expect(screen.queryByText("Beta conversation")).toBeNull();
    });
    expect(screen.getByText("Alpha conversation")).toBeTruthy();
  });

  it("shows no-match message when filter has no results", async () => {
    render(<ConversationList />);
    await waitFor(() => {
      expect(screen.getByTestId("conversation-filter-input")).toBeTruthy();
    });

    fireEvent.change(screen.getByTestId("conversation-filter-input"), {
      target: { value: "zzz_no_match" },
    });

    await waitFor(() => {
      expect(screen.getByText("No conversations match your search.")).toBeTruthy();
    });
  });
});

describe("ConversationList — R7-3 rename (optimistic)", () => {
  it("shows rename input on pencil click", async () => {
    render(<ConversationList />);
    await waitFor(() => {
      const renameBtns = screen.getAllByTestId("conv-rename-btn");
      expect(renameBtns.length).toBeGreaterThan(0);
    });

    fireEvent.click(screen.getAllByTestId("conv-rename-btn")[0]!);

    await waitFor(() => {
      expect(screen.getByTestId("conv-rename-input")).toBeTruthy();
    });
  });

  it("optimistically updates store then calls PATCH on Enter", async () => {
    vi.mocked(chatClient.renameConversation).mockResolvedValue({
      id: "c1",
      vault_id: "v1",
      title: "New Alpha name",
      created_at: "2026-01-01",
      updated_at: "2026-01-01",
    });

    render(<ConversationList />);
    await waitFor(() => {
      expect(screen.getAllByTestId("conv-rename-btn").length).toBeGreaterThan(0);
    });

    fireEvent.click(screen.getAllByTestId("conv-rename-btn")[0]!);
    await waitFor(() => {
      expect(screen.getByTestId("conv-rename-input")).toBeTruthy();
    });

    fireEvent.change(screen.getByTestId("conv-rename-input"), {
      target: { value: "New Alpha name" },
    });
    fireEvent.keyDown(screen.getByTestId("conv-rename-input"), { key: "Enter" });

    // Optimistic update fires first
    await waitFor(() => {
      expect(mockUpdateConversation).toHaveBeenCalledWith("c1", { title: "New Alpha name" });
    });

    // Then PATCH
    await waitFor(() => {
      expect(chatClient.renameConversation).toHaveBeenCalledWith("c1", "New Alpha name");
    });
  });

  it("rolls back store on PATCH error", async () => {
    const { showToast } = await import("../components/common/Toast");
    vi.mocked(chatClient.renameConversation).mockRejectedValue(new Error("network error"));

    render(<ConversationList />);
    await waitFor(() => {
      expect(screen.getAllByTestId("conv-rename-btn").length).toBeGreaterThan(0);
    });

    fireEvent.click(screen.getAllByTestId("conv-rename-btn")[0]!);
    await waitFor(() => {
      expect(screen.getByTestId("conv-rename-input")).toBeTruthy();
    });

    fireEvent.change(screen.getByTestId("conv-rename-input"), {
      target: { value: "Bad name" },
    });
    fireEvent.keyDown(screen.getByTestId("conv-rename-input"), { key: "Enter" });

    // Wait for error handling
    await waitFor(() => {
      expect(chatClient.renameConversation).toHaveBeenCalled();
    });
    await waitFor(() => {
      // Rollback: updateConversation called twice (once optimistic, once rollback)
      expect(mockUpdateConversation).toHaveBeenCalledTimes(2);
    });
    // Error toast
    await waitFor(() => {
      expect(showToast).toHaveBeenCalledWith("Failed to rename conversation", "error");
    });
  });

  it("cancels rename on Esc without calling PATCH", async () => {
    render(<ConversationList />);
    await waitFor(() => {
      expect(screen.getAllByTestId("conv-rename-btn").length).toBeGreaterThan(0);
    });

    fireEvent.click(screen.getAllByTestId("conv-rename-btn")[0]!);
    await waitFor(() => {
      expect(screen.getByTestId("conv-rename-input")).toBeTruthy();
    });

    fireEvent.keyDown(screen.getByTestId("conv-rename-input"), { key: "Escape" });

    await waitFor(() => {
      expect(screen.queryByTestId("conv-rename-input")).toBeNull();
    });
    expect(chatClient.renameConversation).not.toHaveBeenCalled();
  });
});

// ─── UXB-1: preview snippet ───────────────────────────────────────────────────

describe("ConversationList — UXB-1 preview snippet (AC-UXB1-3)", () => {
  it("renders conv-preview when conv.preview is a non-empty string", async () => {
    vi.mocked(useConversations).mockReturnValue([
      {
        id: "c1",
        vault_id: "v1",
        title: "Alpha conversation",
        created_at: "2026-01-01",
        updated_at: "2026-01-01",
        preview: "This is a short preview of the last message",
      },
    ]);

    render(<ConversationList />);

    await waitFor(() => {
      expect(screen.getByTestId("conv-preview")).toBeTruthy();
    });
    expect(screen.getByTestId("conv-preview").textContent).toBe(
      "This is a short preview of the last message",
    );
  });

  it("does NOT render conv-preview when conv.preview is null", async () => {
    vi.mocked(useConversations).mockReturnValue([
      {
        id: "c1",
        vault_id: "v1",
        title: "Alpha conversation",
        created_at: "2026-01-01",
        updated_at: "2026-01-01",
        preview: null,
      },
    ]);

    render(<ConversationList />);

    await waitFor(() => {
      expect(screen.getByText("Alpha conversation")).toBeTruthy();
    });
    expect(screen.queryByTestId("conv-preview")).toBeNull();
  });

  it("does NOT render conv-preview when conv.preview is omitted (older server)", async () => {
    vi.mocked(useConversations).mockReturnValue([
      {
        id: "c1",
        vault_id: "v1",
        title: "Alpha conversation",
        created_at: "2026-01-01",
        updated_at: "2026-01-01",
        // preview field omitted entirely
      },
    ]);

    render(<ConversationList />);

    await waitFor(() => {
      expect(screen.getByText("Alpha conversation")).toBeTruthy();
    });
    expect(screen.queryByTestId("conv-preview")).toBeNull();
  });
});
