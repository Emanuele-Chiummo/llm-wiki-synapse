/**
 * ProviderGate.test.tsx — unit tests for the provider gate (P0, gap G-P0-3).
 *
 * Covers:
 *   A. useProviderConfigured hook — configured/unconfigured/loading/error states.
 *   B. ChatSection gate — renders EmptyState when configured=false; hides when configured=true.
 *   C. IngestView gate — same lifecycle.
 *   D. CTA navigates to settings section.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderHook } from "@testing-library/react";

// ─── Mock i18n ────────────────────────────────────────────────────────────────

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const map: Record<string, string> = {
        "providerGate.title": "No provider configured",
        "providerGate.body": "Configure an AI provider in Settings.",
        "providerGate.cta": "Open Settings",
        "ingest.title": "Ingest Activity",
        "ingest.runIngest": "Run Ingest",
        "chat.title": "Chat",
        "common.loading": "Loading…",
      };
      return map[key] ?? key;
    },
    i18n: { changeLanguage: vi.fn() },
  }),
}));

// ─── Mock graphStore ──────────────────────────────────────────────────────────

let mockActiveSection = "chat";
const mockSetActiveSection = vi.fn((s: string) => { mockActiveSection = s; });

vi.mock("../store/graphStore", () => ({
  useGraphStore: (selector: (s: unknown) => unknown) =>
    selector({
      activeSection: mockActiveSection,
      setActiveSection: mockSetActiveSection,
      vaultId: "default",
      selectPage: vi.fn(),
    }),
  selectVaultId: (s: { vaultId: string }) => s.vaultId,
  selectActiveSection: (s: { activeSection: string }) => s.activeSection,
  selectSetActiveSection: (s: { setActiveSection: unknown }) => s.setActiveSection,
  selectSelectPage: (s: { selectPage: unknown }) => s.selectPage,
}));

// ─── Mock stores used by ChatSection/IngestView ───────────────────────────────

vi.mock("../store/chatStore", () => ({
  useChatStore: (selector: (s: unknown) => unknown) =>
    selector({
      activeConversationId: null,
      isStreaming: false,
      messages: [],
      conversations: [],
      streamError: null,
      lastUsage: null,
      streamingContent: "",
      streamingThink: "",
      appendMessage: vi.fn(),
      fetchConversations: vi.fn(),
      createConversation: vi.fn(),
      deleteConversation: vi.fn(),
      setActiveConversation: vi.fn(),
    }),
  selectActiveConversationId: (s: { activeConversationId: unknown }) => s.activeConversationId,
  selectIsStreaming: (s: { isStreaming: boolean }) => s.isStreaming,
  selectMessages: (s: { messages: unknown[] }) => s.messages,
  selectConversations: (s: { conversations: unknown[] }) => s.conversations,
  selectStreamError: (s: { streamError: unknown }) => s.streamError,
  selectLastUsage: (s: { lastUsage: unknown }) => s.lastUsage,
  selectStreamingContent: (s: { streamingContent: string }) => s.streamingContent,
  selectStreamingThink: (s: { streamingThink: string }) => s.streamingThink,
}));

vi.mock("../store/settingsStore", () => ({
  useSettingsStore: (selector: (s: unknown) => unknown) =>
    selector({
      contextWindowTokens: 32768,
      conversationHistoryLength: 10,
      retrievalMode: "standard",
      webSearchEnabled: false,
      skillsEnabled: false,
      anytxtEnabled: false,
    }),
  selectContextWindow: (s: { contextWindowTokens: number }) => s.contextWindowTokens,
  selectConversationHistoryLength: (s: { conversationHistoryLength: number }) =>
    s.conversationHistoryLength,
  selectRetrievalMode: (s: { retrievalMode: string }) => s.retrievalMode,
  selectWebSearchEnabled: (s: { webSearchEnabled: boolean }) => s.webSearchEnabled,
  selectSkillsEnabled: (s: { skillsEnabled: boolean }) => s.skillsEnabled,
  selectAnytxtEnabled: (s: { anytxtEnabled: boolean }) => s.anytxtEnabled,
}));

vi.mock("../store/ingestStore", () => ({
  useIngestStore: (selector: (s: unknown) => unknown) =>
    selector({
      runs: [],
      loading: false,
      error: null,
      selectedRunId: null,
      fetchFresh: vi.fn(),
      startPolling: vi.fn(() => vi.fn()),
      runningCount: 0,
    }),
  selectFetchFresh: (s: { fetchFresh: unknown }) => s.fetchFresh,
  selectStartPolling: (s: { startPolling: unknown }) => s.startPolling,
  selectRunningCount: (s: { runningCount: number }) => s.runningCount,
  selectIngestError: (s: { error: unknown }) => s.error,
  selectIngestLoading: (s: { loading: boolean }) => s.loading,
  useIngestRunningCount: () => 0,
}));

vi.mock("../store/providerStore", () => ({
  useProviderStore: (selector: (s: unknown) => unknown) =>
    selector({ list: [], activeItem: null, loading: false, error: null }),
  selectProviderList: (s: { list: unknown[] }) => s.list,
  selectActiveProvider: (s: { activeItem: unknown }) => s.activeItem,
}));

// Mock heavy sub-components used by ChatSection / IngestView
vi.mock("../components/chat/ConversationList", () => ({ ConversationList: () => <div data-testid="conversation-list" /> }));
vi.mock("../components/chat/MessageList", () => ({ MessageList: (_p: unknown) => <div data-testid="message-list" /> }));
vi.mock("../components/chat/MessageInput", () => ({ MessageInput: (_p: unknown) => <div data-testid="message-input" /> }));
vi.mock("../components/chat/useChatStream", () => ({ useChatStream: () => ({ send: vi.fn(), abort: vi.fn() }) }));
vi.mock("../components/ingest/IngestRunList", () => ({ IngestRunList: (_p: unknown) => <div data-testid="ingest-run-list" /> }));
vi.mock("../components/ingest/UploadZone", () => ({ UploadZone: () => <div data-testid="upload-zone" /> }));
vi.mock("../components/common/Toast", () => ({ showToast: vi.fn(), ToastHost: () => null }));
vi.mock("zustand/react/shallow", () => ({ useShallow: (fn: unknown) => fn }));

// ─── Mock providerClient ──────────────────────────────────────────────────────

import * as providerClientModule from "../api/providerClient";

// ─── Import components under test ─────────────────────────────────────────────

import { useProviderConfigured } from "../hooks/useProviderConfigured";
import { ChatSection } from "../components/chat/ChatSection";
import { IngestView } from "../components/ingest/IngestView";

// ─── A. useProviderConfigured hook ───────────────────────────────────────────

describe("useProviderConfigured hook", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns loading=true and configured=null initially", async () => {
    // Delay resolving to observe loading state
    vi.spyOn(providerClientModule, "fetchProviderConfigs").mockReturnValue(
      new Promise(() => {}), // never resolves
    );

    const { result } = renderHook(() => useProviderConfigured());
    expect(result.current.loading).toBe(true);
    expect(result.current.configured).toBeNull();
  });

  it("returns configured=true when total > 0", async () => {
    vi.spyOn(providerClientModule, "fetchProviderConfigs").mockResolvedValue({
      items: [{ id: "1", scope: "global", operation: null, vault_id: null, provider_type: "api", model_id: null, base_url: null, max_iter: null, token_budget: null, is_fallback: false, created_at: "", updated_at: "" }],
      total: 1,
    });

    const { result } = renderHook(() => useProviderConfigured());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.configured).toBe(true);
    expect(result.current.error).toBeNull();
  });

  it("returns configured=false when total === 0 and items is empty", async () => {
    vi.spyOn(providerClientModule, "fetchProviderConfigs").mockResolvedValue({
      items: [],
      total: 0,
    });

    const { result } = renderHook(() => useProviderConfigured());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.configured).toBe(false);
  });

  it("returns configured=false on fetch error", async () => {
    vi.spyOn(providerClientModule, "fetchProviderConfigs").mockRejectedValue(
      new Error("Network failure"),
    );

    const { result } = renderHook(() => useProviderConfigured());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.configured).toBe(false);
    expect(result.current.error).toBe("Network failure");
  });
});

// ─── B. ChatSection gate ─────────────────────────────────────────────────────

describe("ChatSection — provider gate", () => {
  beforeEach(() => {
    mockActiveSection = "chat";
    mockSetActiveSection.mockClear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders nothing (empty shell) while provider check is loading", async () => {
    vi.spyOn(providerClientModule, "fetchProviderConfigs").mockReturnValue(
      new Promise(() => {}), // never resolves
    );

    await act(async () => {
      render(<ChatSection />);
    });

    // The shell div renders (data-testid="section-chat") but the gate and normal content do not
    const container = screen.getByTestId("section-chat");
    expect(container).toBeTruthy();
    expect(screen.queryByTestId("provider-gate-chat")).toBeNull();
    expect(screen.queryByTestId("conversation-list")).toBeNull();
  });

  it("renders provider gate EmptyState when no provider is configured", async () => {
    vi.spyOn(providerClientModule, "fetchProviderConfigs").mockResolvedValue({
      items: [],
      total: 0,
    });

    await act(async () => {
      render(<ChatSection />);
    });

    await waitFor(() =>
      expect(screen.queryByTestId("provider-gate-chat")).not.toBeNull(),
    );

    expect(screen.getByTestId("provider-gate-chat")).toBeTruthy();
    expect(screen.getByText("No provider configured")).toBeTruthy();
  });

  it("renders normal chat UI when a provider is configured", async () => {
    vi.spyOn(providerClientModule, "fetchProviderConfigs").mockResolvedValue({
      items: [{ id: "1", scope: "global", operation: null, vault_id: null, provider_type: "api", model_id: null, base_url: null, max_iter: null, token_budget: null, is_fallback: false, created_at: "", updated_at: "" }],
      total: 1,
    });

    await act(async () => {
      render(<ChatSection />);
    });

    await waitFor(() =>
      expect(screen.queryByTestId("conversation-list")).not.toBeNull(),
    );

    expect(screen.queryByTestId("provider-gate-chat")).toBeNull();
  });

  it("CTA 'Open Settings' navigates to settings section", async () => {
    vi.spyOn(providerClientModule, "fetchProviderConfigs").mockResolvedValue({
      items: [],
      total: 0,
    });

    const user = userEvent.setup();
    await act(async () => {
      render(<ChatSection />);
    });

    await waitFor(() => screen.getByText("Open Settings"));

    await user.click(screen.getByText("Open Settings"));
    expect(mockSetActiveSection).toHaveBeenCalledWith("settings");
  });
});

// ─── C. IngestView gate ───────────────────────────────────────────────────────

describe("IngestView — provider gate", () => {
  beforeEach(() => {
    mockActiveSection = "ingest";
    mockSetActiveSection.mockClear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders provider gate when no provider is configured", async () => {
    vi.spyOn(providerClientModule, "fetchProviderConfigs").mockResolvedValue({
      items: [],
      total: 0,
    });

    await act(async () => {
      render(<IngestView />);
    });

    await waitFor(() =>
      expect(screen.queryByTestId("provider-gate-ingest")).not.toBeNull(),
    );

    expect(screen.getByTestId("provider-gate-ingest")).toBeTruthy();
    expect(screen.getByText("No provider configured")).toBeTruthy();
  });

  it("hides gate and renders normal ingest UI when provider is configured", async () => {
    vi.spyOn(providerClientModule, "fetchProviderConfigs").mockResolvedValue({
      items: [{ id: "2", scope: "global", operation: null, vault_id: null, provider_type: "local", model_id: "llama3", base_url: null, max_iter: null, token_budget: null, is_fallback: false, created_at: "", updated_at: "" }],
      total: 1,
    });

    await act(async () => {
      render(<IngestView />);
    });

    await waitFor(() =>
      expect(screen.queryByTestId("ingest-run-list")).not.toBeNull(),
    );

    expect(screen.queryByTestId("provider-gate-ingest")).toBeNull();
  });

  it("CTA navigates to settings section", async () => {
    vi.spyOn(providerClientModule, "fetchProviderConfigs").mockResolvedValue({
      items: [],
      total: 0,
    });

    const user = userEvent.setup();
    await act(async () => {
      render(<IngestView />);
    });

    await waitFor(() => screen.getByText("Open Settings"));
    await user.click(screen.getByText("Open Settings"));
    expect(mockSetActiveSection).toHaveBeenCalledWith("settings");
  });
});
