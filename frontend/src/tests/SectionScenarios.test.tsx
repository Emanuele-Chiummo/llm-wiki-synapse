/**
 * SectionScenarios.test.tsx — vitest tests for R7-1 (FE) Scenario picker in SettingsPanel.
 *
 * Tests:
 *   - Renders loading state on mount
 *   - Renders scenario cards after fetchScenarios resolves
 *   - Shows loadError on fetch failure
 *   - Clicking Apply opens ConfirmDialog
 *   - Confirming calls applyScenario + shows success toast
 *   - Cancelling dialog does NOT call applyScenario
 *
 * INVARIANT I3: no Zustand store subscriptions in this section component.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

// ─── Mock scenariosClient ─────────────────────────────────────────────────────

vi.mock("../api/scenariosClient", () => ({
  fetchScenarios: vi.fn(),
  applyScenario: vi.fn(),
}));

import * as scenariosClient from "../api/scenariosClient";

// ─── Mock ConfirmDialog ───────────────────────────────────────────────────────

vi.mock("../components/common/ConfirmDialog", () => ({
  ConfirmDialog: ({
    title,
    confirmLabel,
    cancelLabel,
    onConfirm,
    onCancel,
  }: {
    title: string;
    body: string;
    confirmLabel: string;
    cancelLabel: string;
    onConfirm: () => void;
    onCancel: () => void;
  }) => (
    <div data-testid="confirm-dialog">
      <span data-testid="confirm-dialog-title">{title}</span>
      <button data-testid="confirm-dialog-confirm" onClick={onConfirm}>
        {confirmLabel}
      </button>
      <button data-testid="confirm-dialog-cancel" onClick={onCancel}>
        {cancelLabel}
      </button>
    </div>
  ),
}));

// ─── Mock Toast ───────────────────────────────────────────────────────────────

vi.mock("../components/common/Toast", () => ({
  showToast: vi.fn(),
}));

// ─── Mock all heavy provider/settings/graph stores used by SettingsPanel ──────
// We need all the imports SettingsPanel makes even though SectionScenarios doesn't use them.

vi.mock("../store/settingsStore", () => ({
  useSettingsStore: vi.fn((selector: (s: unknown) => unknown) =>
    selector({
      contextWindow: 32768,
      contextWindowTokens: 32768,
      language: "en",
      conversationHistoryLength: 20,
      theme: "system",
      // draft fields — identical to committed so isDirty=false → footer hidden
      draftContextWindowTokens: 32768,
      draftConversationHistoryLength: 20,
      draftLanguage: "en",
      draftTheme: "system",
      setContextWindow: vi.fn(),
      setLanguage: vi.fn(),
      setConversationHistoryLength: vi.fn(),
      setTheme: vi.fn(),
      setDraftContextWindow: vi.fn(),
      setDraftConversationHistoryLength: vi.fn(),
      setDraftLanguage: vi.fn(),
      setDraftTheme: vi.fn(),
      commitDraft: vi.fn(),
      discardDraft: vi.fn(),
      resetSettings: vi.fn(),
    }),
  ),
  selectContextWindow: (s: { contextWindow: number }) => s.contextWindow,
  selectLanguage: (s: { language: string }) => s.language,
  selectConversationHistoryLength: (s: { conversationHistoryLength: number }) =>
    s.conversationHistoryLength,
  selectSetContextWindow: (s: { setContextWindow: () => void }) => s.setContextWindow,
  selectSetLanguage: (s: { setLanguage: () => void }) => s.setLanguage,
  selectSetConversationHistoryLength: (s: { setConversationHistoryLength: () => void }) =>
    s.setConversationHistoryLength,
  selectResetSettings: (s: { resetSettings: () => void }) => s.resetSettings,
  selectTheme: (s: { theme: string }) => s.theme,
  selectSetTheme: (s: { setTheme: () => void }) => s.setTheme,
  // draft selectors (required by SettingsSaveFooter mounted inside SettingsPanel)
  selectDraftContextWindow: (s: { draftContextWindowTokens: number }) => s.draftContextWindowTokens,
  selectDraftConversationHistoryLength: (s: { draftConversationHistoryLength: number }) =>
    s.draftConversationHistoryLength,
  selectDraftLanguage: (s: { draftLanguage: string }) => s.draftLanguage,
  selectDraftTheme: (s: { draftTheme: string }) => s.draftTheme,
  selectSetDraftContextWindow: (s: { setDraftContextWindow: () => void }) =>
    s.setDraftContextWindow,
  selectSetDraftConversationHistoryLength: (s: { setDraftConversationHistoryLength: () => void }) =>
    s.setDraftConversationHistoryLength,
  selectSetDraftLanguage: (s: { setDraftLanguage: () => void }) => s.setDraftLanguage,
  selectSetDraftTheme: (s: { setDraftTheme: () => void }) => s.setDraftTheme,
  selectCommitDraft: (s: { commitDraft: () => void }) => s.commitDraft,
  selectDiscardDraft: (s: { discardDraft: () => void }) => s.discardDraft,
  selectIsDirty: (s: {
    draftTheme: string;
    theme: string;
    draftLanguage: string;
    language: string;
    draftConversationHistoryLength: number;
    conversationHistoryLength: number;
    draftContextWindowTokens: number;
    contextWindowTokens: number;
  }) =>
    s.draftTheme !== s.theme ||
    s.draftLanguage !== s.language ||
    s.draftConversationHistoryLength !== s.conversationHistoryLength ||
    s.draftContextWindowTokens !== s.contextWindowTokens,
  CONTEXT_WINDOW_OPTIONS: [4096, 32768, 131072],
  CONV_HISTORY_OPTIONS: [10, 20, 50],
  computeBudgetSplit: () => ({ history: 100, retrieved: 50, system: 20, generation: 60 }),
  formatTokenCount: (n: number) => `${n}`,
}));

vi.mock("../store/providerStore", () => ({
  useProviderStore: vi.fn((selector: (s: unknown) => unknown) =>
    selector({
      list: [],
      activeItem: null,
      loading: false,
      error: null,
      writeScope: "global",
      vendors: [],
      vendorsLoading: false,
      vendorsError: null,
      fetchList: vi.fn(),
      addProvider: vi.fn(),
      deleteProvider: vi.fn(),
      fetchVendorCatalog: vi.fn(),
      updateProvider: vi.fn(),
    }),
  ),
  useProviderList: () => [],
  useVendorList: () => [],
  selectProviderList: (s: { list: unknown[] }) => s.list,
  selectProviderLoading: (s: { loading: boolean }) => s.loading,
  selectProviderError: (s: { error: string | null }) => s.error,
  selectActiveProvider: (s: { activeItem: unknown }) => s.activeItem,
  selectFetchProviderList: (s: { fetchList: unknown }) => s.fetchList,
  selectAddProvider: (s: { addProvider: unknown }) => s.addProvider,
  selectDeleteProvider: (s: { deleteProvider: unknown }) => s.deleteProvider,
  selectVendors: (s: { vendors: unknown[] }) => s.vendors,
  selectVendorsLoading: (s: { vendorsLoading: boolean }) => s.vendorsLoading,
  selectVendorsError: (s: { vendorsError: string | null }) => s.vendorsError,
  selectFetchVendorCatalog: (s: { fetchVendorCatalog: unknown }) => s.fetchVendorCatalog,
  selectUpdateProvider: (s: { updateProvider: unknown }) => s.updateProvider,
}));

vi.mock("../store/graphStore", () => ({
  useGraphStore: vi.fn((selector: (s: unknown) => unknown) => selector({ vaultId: "v1" })),
  selectVaultId: (s: { vaultId: string }) => s.vaultId,
}));

vi.mock("../api/providerClient", () => ({
  fetchEmbeddingConfig: vi.fn().mockResolvedValue({
    embeddings_enabled: false,
    embedding_url: "",
    embedding_model: "",
    embedding_dim: 768,
  }),
  fetchMcpInfo: vi.fn().mockResolvedValue({
    server_name: "synapse",
    transport: "stdio",
    entry_point_command: "python -m app",
    tool_count: 0,
    tools: [],
    http_enabled: false,
    remote_write_enabled: false,
    token_configured: false,
    remote_enabled: false,
    mount_path: "/mcp",
    token_source: "none",
    allow_without_token: false,
  }),
  setRemoteMcpEnabled: vi.fn(),
  setMcpAuth: vi.fn(),
  fetchClipConfig: vi.fn().mockResolvedValue({
    enabled: false,
    token_configured: false,
    token_source: "none",
    allowed_origins: [],
    max_body_bytes: 1048576,
  }),
  setClipConfig: vi.fn(),
  fetchWebSearchConfig: vi.fn().mockResolvedValue({
    configured: false,
    url: null,
    categories: [],
    max_queries: 3,
    source: "env",
  }),
  setWebSearchConfig: vi.fn(),
  getCliAuthConfig: vi.fn().mockResolvedValue({
    token_configured: false,
    token_source: "none",
    auth_mode: "unconfigured",
  }),
  setCliAuthConfig: vi.fn(),
  fetchVendors: vi.fn().mockResolvedValue({ vendors: [] }),
  fetchProviderConfigs: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  createProviderConfig: vi.fn(),
  updateProviderConfig: vi.fn(),
  deleteProviderConfig: vi.fn(),
  testProviderConnection: vi.fn(),
  testProviderFunction: vi.fn(),
}));

vi.mock("../components/settings/ImportScheduleCard", () => ({
  ImportScheduleCard: () => <div data-testid="import-schedule-card" />,
}));

// R11-2: mock appConfigClient so SectionRuntimeConfig doesn't throw on fetch
vi.mock("../api/appConfigClient", () => ({
  getAppConfig: vi.fn().mockResolvedValue({
    settings: [
      { key: "pdf_extractor", value: "pypdf", source: "env" },
      { key: "marker_service_url", value: "", source: "env" },
      { key: "marker_timeout_seconds", value: "60", source: "env" },
      { key: "cost_alert_threshold_usd", value: "5.0", source: "env" },
      { key: "embeddings_enabled", value: "true", source: "env" },
      { key: "embedding_format", value: "ollama", source: "env" },
      { key: "overview_language", value: "en", source: "env" },
      { key: "wikilink_enrich_enabled", value: "true", source: "env" },
    ],
  }),
  putAppConfig: vi.fn().mockResolvedValue(undefined),
  resetAppConfig: vi.fn().mockResolvedValue(undefined),
}));

// ─── i18n mock ────────────────────────────────────────────────────────────────

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      const map: Record<string, string> = {
        "settings.title": "Settings",
        "settings.scenarios.title": "Scenario Templates",
        "settings.scenarios.desc": "Apply a predefined scenario to quickly configure your vault.",
        "settings.scenarios.apply": "Apply",
        "settings.scenarios.applyConfirmTitle": "Apply scenario?",
        "settings.scenarios.applyConfirmBody": `Applying "${String(params?.name ?? "")}" will overwrite purpose.md and schema.md.`,
        "settings.scenarios.applyConfirm": "Apply scenario",
        "settings.scenarios.applyCancel": "Cancel",
        "settings.scenarios.applying": "Applying…",
        "settings.scenarios.applied": "Scenario applied successfully",
        "settings.scenarios.loadError": "Could not load scenarios. Is the backend running?",
        "common.loading": "Loading…",
        // A2.1: 5-group nav items (replaced 14 flat sections)
        "settings.nav.groupGettingStarted": "Getting started",
        "settings.nav.groupAiModels": "AI & Models",
        "settings.nav.groupSources": "Sources & PDF",
        "settings.nav.groupOutput": "Output & Appearance",
        "settings.nav.groupAdvanced": "Advanced",
        // Section headers still used inside groups
        "settings.nav.general": "General",
        "settings.nav.llmModels": "LLM Models",
        "settings.nav.embeddings": "Embeddings",
        "settings.nav.sourceWatch": "Source Watch",
        "settings.nav.webSearch": "Web Search",
        "settings.nav.apiMcp": "API + MCP",
        "settings.nav.webClipper": "Web Clipper",
        "settings.nav.output": "Output",
        "settings.nav.interface": "Interface",
        "settings.nav.maintenance": "Maintenance",
        "settings.nav.about": "About",
        "settings.nav.scenarios": "Scenarios",
        // Runtime config keys (R11-2 / A2.1)
        "config.pdfExtractorSection.title": "PDF extraction",
        "config.pdfExtractorSection.desc": "Configure the PDF extraction engine.",
        "config.runtimeOverridesSection.title": "Runtime overrides",
        "config.runtimeOverridesSection.desc": "Override env-var defaults at runtime.",
        "config.gettingStarted.wizardSlot": "Setup wizard",
        "config.gettingStarted.wizardSlotDesc": "Step-by-step vault configuration.",
        "config.gettingStarted.wizardComingSoon": "Coming soon",
        "config.loading": "Loading…",
        "config.error": "Failed to load config.",
      };
      return map[key] ?? key;
    },
    i18n: { language: "en" },
  }),
  useShallow: (fn: (s: unknown) => unknown) => fn,
}));

// ─── Import component after mocks ─────────────────────────────────────────────

// We import the full SettingsPanel and navigate to the Scenarios section,
// which is the realistic integration path.
import { SettingsPanel } from "../components/settings/SettingsPanel";

// ─── Sample data ──────────────────────────────────────────────────────────────

const SAMPLE_SCENARIOS = [
  { id: "homelab", name: "Homelab", description: "Self-hosted infrastructure docs" },
  { id: "research", name: "Research", description: "Academic research notes" },
];

// ─── Helpers ──────────────────────────────────────────────────────────────────

async function openScenariosSection() {
  render(<SettingsPanel />);
  // ADR-0055: SectionScenarios is on page "scenarios" in the 2-level nav.
  await waitFor(() => {
    expect(document.querySelector('[data-settings-section="scenarios"]')).not.toBeNull();
  });
  const scenariosBtn = document.querySelector('[data-settings-section="scenarios"]') as HTMLElement;
  fireEvent.click(scenariosBtn);
  // SectionScenarios title "Scenario Templates" should appear
  await waitFor(() => {
    expect(screen.getAllByText("Scenario Templates").length).toBeGreaterThan(0);
  });
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe("SectionScenarios — R7-1 (FE)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows loading while fetchScenarios is pending", async () => {
    vi.mocked(scenariosClient.fetchScenarios).mockReturnValue(
      new Promise(() => {
        /* never resolves */
      }),
    );
    await openScenariosSection();
    await waitFor(() => {
      expect(screen.getByText("Loading…")).toBeTruthy();
    });
  });

  it("renders scenario cards after fetchScenarios resolves", async () => {
    vi.mocked(scenariosClient.fetchScenarios).mockResolvedValue(SAMPLE_SCENARIOS);

    await openScenariosSection();

    await waitFor(() => {
      expect(screen.getByText("Homelab")).toBeTruthy();
      expect(screen.getByText("Research")).toBeTruthy();
    });
    expect(screen.getAllByTestId("scenario-card").length).toBe(2);
  });

  it("shows loadError on fetch failure", async () => {
    vi.mocked(scenariosClient.fetchScenarios).mockRejectedValue(new Error("network error"));

    await openScenariosSection();

    await waitFor(() => {
      expect(screen.getByText("Could not load scenarios. Is the backend running?")).toBeTruthy();
    });
  });

  it("opens ConfirmDialog when Apply is clicked", async () => {
    vi.mocked(scenariosClient.fetchScenarios).mockResolvedValue(SAMPLE_SCENARIOS);

    await openScenariosSection();
    await waitFor(() => {
      expect(screen.getAllByTestId("scenario-apply-btn").length).toBeGreaterThan(0);
    });

    fireEvent.click(screen.getAllByTestId("scenario-apply-btn")[0]!);

    await waitFor(() => {
      expect(screen.getByTestId("confirm-dialog")).toBeTruthy();
    });
    expect(screen.getByTestId("confirm-dialog-title")).toBeTruthy();
  });

  it("calls applyScenario + shows success toast on confirm", async () => {
    vi.mocked(scenariosClient.fetchScenarios).mockResolvedValue(SAMPLE_SCENARIOS);
    vi.mocked(scenariosClient.applyScenario).mockResolvedValue({ applied: true });
    const { showToast } = await import("../components/common/Toast");

    await openScenariosSection();
    await waitFor(() => {
      expect(screen.getAllByTestId("scenario-apply-btn").length).toBeGreaterThan(0);
    });

    fireEvent.click(screen.getAllByTestId("scenario-apply-btn")[0]!);
    await waitFor(() => {
      expect(screen.getByTestId("confirm-dialog-confirm")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));

    await waitFor(() => {
      expect(scenariosClient.applyScenario).toHaveBeenCalledWith("homelab");
    });
    await waitFor(() => {
      expect(showToast).toHaveBeenCalledWith("Scenario applied successfully", "success");
    });
  });

  it("closes dialog without calling applyScenario on cancel", async () => {
    vi.mocked(scenariosClient.fetchScenarios).mockResolvedValue(SAMPLE_SCENARIOS);

    await openScenariosSection();
    await waitFor(() => {
      expect(screen.getAllByTestId("scenario-apply-btn").length).toBeGreaterThan(0);
    });

    fireEvent.click(screen.getAllByTestId("scenario-apply-btn")[0]!);
    await waitFor(() => {
      expect(screen.getByTestId("confirm-dialog-cancel")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("confirm-dialog-cancel"));

    await waitFor(() => {
      expect(screen.queryByTestId("confirm-dialog")).toBeNull();
    });
    expect(scenariosClient.applyScenario).not.toHaveBeenCalled();
  });
});
