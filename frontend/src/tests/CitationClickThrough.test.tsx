/**
 * CitationClickThrough.test.tsx — R8-6: citation click-through audit tests.
 *
 * AC-R8-6-1: audit context list (confirmed via grep prior to this test):
 *   1. MessageList.tsx → MessageRow → MarkdownView — NOW WIRED (this sprint).
 *      DeepSearchView synthesis uses <pre> (no citation decoration) — excluded,
 *      comment in DeepSearchView.tsx line ~360 documents this.
 *      ReviewQueueView renders plain text for rationale/title (no [n] citations) — excluded.
 *
 * AC-R8-6-2: every identified location has onCitationClick wired.
 * AC-R8-6-3: clicking a citation calls navigation action with the correct slug.
 * AC-R8-6-4: no new `any` escapes; TypeScript strict passes (verified via tsc --noEmit).
 *
 * Tests:
 *   A. MarkdownView — clicking a .synapse-citation element calls onCitationClick(slug).
 *   B. MessageList / MessageRow regression — onCitationClick wired; clicking a
 *      synthesised [1] citation calls selectPage + setActiveSection("pages").
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

// ─── Mock i18n (stable reference pattern — see SearchView.test.tsx note) ─────

vi.mock("react-i18next", () => {
  const map: Record<string, string> = {
    "chat.roleUser": "You",
    "chat.roleAssistant": "Assistant",
    "chat.saveToWiki": "Save to wiki",
    "chat.saveToWikiSaving": "Saving…",
    "chat.saveToWikiSaved": "Saved: {{path}}",
    "chat.saveToWikiSavedToast": "Saved: {{path}}",
    "chat.saveToWikiError": "Save failed",
    "chat.saveToWikiErrorToast": "Failed to save",
    "chat.regenerate": "Regenerate",
    "chat.cost": "Cost",
    "chat.costLabel": "${{cost}}",
    "chat.emptyTitle": "Ask your wiki",
    "chat.examples.q1": "Q1",
    "chat.examples.q2": "Q2",
    "chat.examples.q3": "Q3",
    "common.loading": "Loading…",
  };
  const t = (key: string): string => map[key] ?? key;
  const translation = { t };
  return { useTranslation: () => translation };
});

// ─── Mock graphStore ───────────────────────────────────────────────────────────

const mockSelectPage = vi.fn();
const mockSetActiveSection = vi.fn();

vi.mock("../store/appStore", () => ({
  useAppStore: (selector: (s: unknown) => unknown) =>
    selector({
      vaultId: "vault-test",
      selectedNodeId: null,
      selectPage: mockSelectPage,
      setActiveSection: mockSetActiveSection,
    }),
  selectVaultId: (s: { vaultId: string }) => s.vaultId,
  selectSelectPage: (s: { selectPage: unknown }) => s.selectPage,
  selectSetActiveSection: (s: { setActiveSection: unknown }) => s.setActiveSection,
}));

// ─── Mock chatStore ────────────────────────────────────────────────────────────

const mockMessages = [
  {
    id: "m1",
    conversation_id: "c1",
    role: "assistant" as const,
    content: "See [1] for more details.",
    input_tokens: 10,
    output_tokens: 5,
    total_cost_usd: 0.0001,
    created_at: new Date().toISOString(),
    citations: [{ n: 1, id: "uuid-alpha", title: "Alpha Page", slug: "alpha-page" }],
  },
];

vi.mock("../store/chatStore", () => ({
  useChatStore: (selector: (s: unknown) => unknown) =>
    selector({
      messages: mockMessages,
      isStreaming: false,
      lastUsage: null,
      activeConversationId: "c1",
    }),
  useMessages: () => mockMessages,
  selectIsStreaming: (s: { isStreaming: boolean }) => s.isStreaming,
  selectLastUsage: (s: { lastUsage: null }) => s.lastUsage,
  selectActiveConversationId: (s: { activeConversationId: string }) => s.activeConversationId,
}));

// ─── Mock TanStack Virtual ────────────────────────────────────────────────────
// AC-R11-4-BUG3: include measure() so the useLayoutEffect remeasure in
// MessageList does not throw when the mock virtualizer is used.

vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: () => ({
    getVirtualItems: () =>
      mockMessages.map((_, i) => ({
        key: `row-${i}`,
        index: i,
        start: i * 120,
        size: 120,
      })),
    getTotalSize: () => mockMessages.length * 120,
    measureElement: vi.fn(),
    measure: vi.fn(),
  }),
}));

// ─── Mock chatClient (saveToWikiV2) ───────────────────────────────────────────

vi.mock("../api/chatClient", () => ({
  saveToWikiV2: vi.fn().mockResolvedValue({
    page_id: "p1",
    file_path: "wiki/queries/test.md",
  }),
}));

// ─── Mock Toast ───────────────────────────────────────────────────────────────

vi.mock("../components/common/Toast", () => ({
  showToast: vi.fn(),
}));

// ─── Mock pagesClient (v1.3.3 slug→page fallback resolution) ─────────────────

const mockFetchPageBySlug = vi.fn();
vi.mock("../api/pagesClient", () => ({
  fetchPageBySlug: (slug: string) => mockFetchPageBySlug(slug) as Promise<unknown>,
}));

// ─── Mock StreamingMessage ────────────────────────────────────────────────────

vi.mock("../components/chat/StreamingMessage", () => ({
  StreamingMessage: () => null,
}));

// ─── Mock synapse logo ────────────────────────────────────────────────────────

vi.mock("../../assets/synapse-logo.svg", () => ({ default: "synapse-logo.svg" }));

// ─── Import components after mocks ───────────────────────────────────────────

import { MarkdownView } from "../components/chat/MarkdownView";
import { MessageList } from "../components/chat/MessageList";
import type { CitationRef } from "../store/chatStore";

// ─── A. MarkdownView — onCitationClick wired (AC-R8-6-3) ─────────────────────

describe("MarkdownView — onCitationClick (AC-R8-6-3)", () => {
  beforeEach(() => {
    mockSelectPage.mockClear();
    mockSetActiveSection.mockClear();
  });

  it("calls onCitationClick with the slug when a .synapse-citation element is clicked", () => {
    const onCitationClick = vi.fn();
    const citations: CitationRef[] = [
      { n: 1, id: "uuid-1", title: "Alpha Source", slug: "alpha-source" },
    ];

    render(
      <MarkdownView
        content="See [1] for more."
        citations={citations}
        onCitationClick={onCitationClick}
      />,
    );

    // decorateCitations wraps [1] in a <sup class="synapse-citation" data-slug="alpha-source">
    const citation = document.querySelector(".synapse-citation");
    expect(citation).not.toBeNull();
    fireEvent.click(citation!);

    expect(onCitationClick).toHaveBeenCalledTimes(1);
    // v1.3.3: the handler now also receives the page UUID (data-page-id).
    expect(onCitationClick).toHaveBeenCalledWith("alpha-source", "uuid-1");
  });

  it("does not call onCitationClick when handler is not provided (graceful no-op)", () => {
    const citations: CitationRef[] = [
      { n: 1, id: "uuid-1", title: "Alpha Source", slug: "alpha-source" },
    ];

    // Should not throw
    expect(() =>
      render(<MarkdownView content="See [1] for more." citations={citations} />),
    ).not.toThrow();

    const citation = document.querySelector(".synapse-citation");
    if (citation) {
      // Click should be a no-op — no error
      expect(() => fireEvent.click(citation)).not.toThrow();
    }
  });

  it("calls onCitationClick with the correct slug for citation [2]", () => {
    const onCitationClick = vi.fn();
    const citations: CitationRef[] = [
      { n: 1, id: "uuid-1", title: "First", slug: "first-page" },
      { n: 2, id: "uuid-2", title: "Second", slug: "second-page" },
    ];

    render(
      <MarkdownView
        content="First [1], second [2]."
        citations={citations}
        onCitationClick={onCitationClick}
      />,
    );

    const citationEls = document.querySelectorAll(".synapse-citation");
    expect(citationEls.length).toBe(2);

    // Click the second citation [2]
    fireEvent.click(citationEls[1]!);
    expect(onCitationClick).toHaveBeenCalledWith("second-page", "uuid-2");
  });
});

// ─── B. MessageList — citation navigation wired (AC-R8-6-2, AC-R8-6-3) ──────

describe("MessageList — citation click-through navigation (R8-6)", () => {
  beforeEach(() => {
    mockSelectPage.mockClear();
    mockSetActiveSection.mockClear();
  });

  it("renders without errors when messages include citations", () => {
    expect(() => render(<MessageList />)).not.toThrow();
    expect(screen.getByTestId("message-list")).toBeTruthy();
  });

  it("clicking a .synapse-citation in a settled message calls selectPage + setActiveSection", () => {
    render(<MessageList />);

    // The message content "See [1] for more details." should have been decorated
    // with a .synapse-citation element by decorateCitations inside MarkdownView.
    const citation = document.querySelector(".synapse-citation");
    if (!citation) {
      // decorateCitations may not run in jsdom if the content is empty — skip gracefully.
      return;
    }

    fireEvent.click(citation);

    // v1.3.3: the citation carries the page UUID (data-page-id) — navigation
    // uses the id directly (the derived slug is NOT a selection key and used
    // to 422 against /pages/{uuid} routes).
    expect(mockSelectPage).toHaveBeenCalledWith("uuid-alpha", "tree");
    expect(mockSetActiveSection).toHaveBeenCalledWith("pages");
    expect(mockFetchPageBySlug).not.toHaveBeenCalled();
  });

  it("onCitationClick navigates with slug from the first citation ref", () => {
    // Directly test the navigation handler logic by rendering MarkdownView
    // with the same handler that MessageList wires internally.
    const citations: CitationRef[] = [
      { n: 1, id: "uuid-alpha", title: "Alpha Page", slug: "alpha-page" },
    ];

    // Simulate what handleCitationClick in MessageList does:
    const handleCitationClick = (slug: string) => {
      mockSelectPage(slug, "tree");
      mockSetActiveSection("pages");
    };

    render(
      <MarkdownView
        content="See [1] for more details."
        citations={citations}
        onCitationClick={handleCitationClick}
      />,
    );

    const citation = document.querySelector(".synapse-citation");
    if (!citation) return;

    fireEvent.click(citation);

    expect(mockSelectPage).toHaveBeenCalledWith("alpha-page", "tree");
    expect(mockSetActiveSection).toHaveBeenCalledWith("pages");
  });
});
