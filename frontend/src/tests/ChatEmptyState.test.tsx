/**
 * ChatEmptyState.test.tsx — unit tests for the branded chat empty state (ADR-0048 §T3).
 *
 * Covers:
 *   A. Empty state renders when messages=0 and not streaming: logo img, title, 3 chips.
 *   B. Clicking a chip calls onSend with the chip text.
 *   C. Empty state does NOT render while streaming (I3 guard).
 *   D. Empty state does NOT render when messages are present.
 *
 * Mocking pattern: re-set vi.fn() implementations in each beforeEach because
 * vi.clearAllMocks() (used by some test runners) wipes implementations.
 *
 * PROJECT GOTCHA: vi.clearAllMocks() wipes mock impls — re-set in each beforeEach.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MessageList } from "../components/chat/MessageList";

// ─── Mocks ────────────────────────────────────────────────────────────────────

// Minimal i18n mock returning expected strings for the empty state keys.
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const map: Record<string, string> = {
        "chat.emptyTitle": "Ask your wiki, not just a model",
        "chat.examples.q1": "Map the strongest concepts in this vault",
        "chat.examples.q2": "Summarize what changed in the latest sources",
        "chat.examples.q3": "Find gaps that need deep research",
        "chat.roleUser": "You",
        "chat.roleAssistant": "Assistant",
        "chat.saveToWiki": "Save to wiki",
        "chat.saveToWikiSaving": "Saving…",
        "chat.saveToWikiSaved": "Saved: {{path}}",
        "chat.regenerate": "Regenerate",
        "chat.cost": "Cost",
        "chat.costLabel": "${{cost}}",
      };
      return map[key] ?? key;
    },
    i18n: { changeLanguage: vi.fn() },
  }),
}));

// chatStore mock: configurable per-test; default = empty, not streaming.
let mockMessages: unknown[] = [];
let mockIsStreaming = false;
let mockLastUsage: unknown = null;
let mockActiveConversationId: string | null = null;

vi.mock("../store/chatStore", () => ({
  useChatStore: (selector: (s: unknown) => unknown) =>
    selector({
      messages: mockMessages,
      isStreaming: mockIsStreaming,
      lastUsage: mockLastUsage,
      activeConversationId: mockActiveConversationId,
      streamingContent: "",
      streamingThink: "",
    }),
  useMessages: () => mockMessages,
  selectIsStreaming: (s: { isStreaming: boolean }) => s.isStreaming,
  selectLastUsage: (s: { lastUsage: unknown }) => s.lastUsage,
  selectActiveConversationId: (s: { activeConversationId: string | null }) =>
    s.activeConversationId,
  selectStreamingContent: (s: { streamingContent: string }) => s.streamingContent,
  selectStreamingThink: (s: { streamingThink: string }) => s.streamingThink,
}));

// graphStore mock — R8-6: must export selectSelectPage + selectSetActiveSection
// because MessageList now imports those selectors for citation click-through.
vi.mock("../store/graphStore", () => ({
  useGraphStore: (selector: (s: unknown) => unknown) =>
    selector({
      vaultId: "test-vault",
      selectPage: vi.fn(),
      setActiveSection: vi.fn(),
    }),
  selectVaultId: (s: { vaultId: string }) => s.vaultId,
  selectSelectPage: (s: { selectPage: unknown }) => s.selectPage,
  selectSetActiveSection: (s: { setActiveSection: unknown }) => s.setActiveSection,
}));

// TanStack Virtual mock — return empty virtualItems for zero messages.
// AC-R11-4-BUG3: include measure() so the useLayoutEffect remeasure in
// MessageList does not throw when the mock virtualizer is used.
vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: () => ({
    getTotalSize: () => 0,
    getVirtualItems: () => [],
    measureElement: vi.fn(),
    measure: vi.fn(),
  }),
}));

// chatClient mock — not used in empty state but imported by MessageList.
vi.mock("../api/chatClient", () => ({
  saveToWikiV2: vi.fn(),
}));

// Toast mock
vi.mock("../components/common/Toast", () => ({
  showToast: vi.fn(),
}));

// SVG asset mock — vitest resolves this relative to the test file.
// MessageList imports "../../assets/synapse-logo.svg" from its own location
// (frontend/src/components/chat/), which resolves to the same module as
// "../assets/synapse-logo.svg" from here (frontend/src/tests/).
vi.mock("../assets/synapse-logo.svg", () => ({
  default: "/assets/synapse-logo.svg",
}));

// ─── Helpers ─────────────────────────────────────────────────────────────────

function renderMessageList(onSend?: ((text: string) => void) | undefined) {
  // Pass onSend only when defined to avoid exactOptionalPropertyTypes error
  return onSend !== undefined
    ? render(<MessageList onSend={onSend} />)
    : render(<MessageList />);
}

// ─── A. Empty state renders correctly ────────────────────────────────────────

describe("ChatEmptyState — renders when no messages and not streaming (ADR-0048 §T3)", () => {
  beforeEach(() => {
    // Re-set mutable state — vi.clearAllMocks() wipes fn impls so we re-declare here.
    mockMessages = [];
    mockIsStreaming = false;
    mockLastUsage = null;
    mockActiveConversationId = null;
  });

  it("renders the chat-empty-state container", () => {
    renderMessageList();
    expect(screen.getByTestId("chat-empty-state")).toBeTruthy();
  });

  it("renders the Synapse logo img", () => {
    renderMessageList();
    const empty = screen.getByTestId("chat-empty-state");
    const img = empty.querySelector("img[alt='Synapse']");
    expect(img).not.toBeNull();
  });

  it("renders the empty-state title", () => {
    renderMessageList();
    expect(screen.getByText("Ask your wiki, not just a model")).toBeTruthy();
  });

  it("renders exactly 3 example-question chips", () => {
    renderMessageList();
    const chips = screen.getAllByTestId("chat-example-chip");
    expect(chips).toHaveLength(3);
  });

  it("chip texts match the i18n keys q1, q2, q3", () => {
    renderMessageList();
    expect(
      screen.getByText("Map the strongest concepts in this vault"),
    ).toBeTruthy();
    expect(
      screen.getByText("Summarize what changed in the latest sources"),
    ).toBeTruthy();
    expect(
      screen.getByText("Find gaps that need deep research"),
    ).toBeTruthy();
  });

  it("chip container has data-testid='chat-example-chips'", () => {
    renderMessageList();
    expect(screen.getByTestId("chat-example-chips")).toBeTruthy();
  });
});

// ─── B. Clicking a chip triggers onSend ──────────────────────────────────────

describe("ChatEmptyState — chip click triggers send action (ADR-0048 §T3)", () => {
  beforeEach(() => {
    mockMessages = [];
    mockIsStreaming = false;
    mockLastUsage = null;
    mockActiveConversationId = null;
  });

  it("clicking the first chip calls onSend with q1 text", () => {
    const onSend = vi.fn();
    renderMessageList(onSend);

    const chips = screen.getAllByTestId("chat-example-chip");
    fireEvent.click(chips[0]!);

    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend).toHaveBeenCalledWith(
      "Map the strongest concepts in this vault",
    );
  });

  it("clicking the second chip calls onSend with q2 text", () => {
    const onSend = vi.fn();
    renderMessageList(onSend);

    const chips = screen.getAllByTestId("chat-example-chip");
    fireEvent.click(chips[1]!);

    expect(onSend).toHaveBeenCalledWith(
      "Summarize what changed in the latest sources",
    );
  });

  it("clicking the third chip calls onSend with q3 text", () => {
    const onSend = vi.fn();
    renderMessageList(onSend);

    const chips = screen.getAllByTestId("chat-example-chip");
    fireEvent.click(chips[2]!);

    expect(onSend).toHaveBeenCalledWith("Find gaps that need deep research");
  });

  it("each chip fires onSend exactly once per click", () => {
    const onSend = vi.fn();
    renderMessageList(onSend);

    const chips = screen.getAllByTestId("chat-example-chip");
    chips.forEach((chip) => fireEvent.click(chip!));

    expect(onSend).toHaveBeenCalledTimes(3);
  });
});

// ─── C. Empty state hidden while streaming ───────────────────────────────────

describe("ChatEmptyState — hidden while streaming (I3 guard)", () => {
  beforeEach(() => {
    mockMessages = [];
    mockIsStreaming = true; // streaming active
    mockLastUsage = null;
    mockActiveConversationId = null;
  });

  it("does NOT render chat-empty-state while isStreaming=true", () => {
    renderMessageList();
    expect(screen.queryByTestId("chat-empty-state")).toBeNull();
  });

  it("does NOT render example chips while streaming", () => {
    renderMessageList();
    expect(screen.queryAllByTestId("chat-example-chip")).toHaveLength(0);
  });
});

// ─── D. Empty state hidden when messages present ─────────────────────────────

describe("ChatEmptyState — hidden when messages are present", () => {
  beforeEach(() => {
    mockMessages = [
      {
        id: "m1",
        conversation_id: "c1",
        role: "user",
        content: "Hello",
        input_tokens: 0,
        output_tokens: 0,
        total_cost_usd: 0,
        created_at: new Date().toISOString(),
        citations: [],
      },
    ];
    mockIsStreaming = false;
    mockLastUsage = null;
    mockActiveConversationId = null;
  });

  it("does NOT render chat-empty-state when messages.length > 0", () => {
    renderMessageList();
    expect(screen.queryByTestId("chat-empty-state")).toBeNull();
  });
});
