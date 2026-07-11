/**
 * SectionCosts.test.tsx — unit tests for the "Costi" settings section (R9-1).
 *
 * Covers:
 *   A. Renders within SettingsPanel when "costs" nav item is clicked.
 *   B. Displays monthly total (data-testid="costs-monthly-total").
 *   C. Shows threshold alert badge when threshold_alert=true
 *      (data-testid="costs-threshold-alert").
 *   D. Alert is NOT rendered when threshold_alert=false.
 *   E. Renders the SVG bar chart (data-testid="costs-day-chart").
 *   F. Renders by_provider table (data-testid="costs-by-provider").
 *   G. Renders by_operation table (data-testid="costs-by-operation").
 *   H. Shows loading state before fetch resolves.
 *   I. Shows error state on fetch failure.
 *
 * INVARIANT I9: no chart library imported — chart is pure SVG.
 * INVARIANT I3: no heavy work on each token; fetch is once on mount.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SettingsPanel } from "../components/settings/SettingsPanel";

// ─── Mock i18n ────────────────────────────────────────────────────────────────

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, _opts?: object) => {
      const parts = key.split(".");
      return parts[parts.length - 1] ?? key;
    },
    i18n: { changeLanguage: vi.fn() },
  }),
}));

// ─── Mock settingsStore ───────────────────────────────────────────────────────

vi.mock("../store/settingsStore", () => ({
  useSettingsStore: (selector: (s: unknown) => unknown) =>
    selector({
      contextWindowTokens: 32768,
      conversationHistoryLength: 10,
      language: "en",
      theme: "system",
      // draft fields — identical to committed so isDirty=false → footer hidden
      draftContextWindowTokens: 32768,
      draftConversationHistoryLength: 10,
      draftLanguage: "en",
      draftTheme: "system",
      setContextWindow: vi.fn(),
      setConversationHistoryLength: vi.fn(),
      setLanguage: vi.fn(),
      setTheme: vi.fn(),
      setDraftContextWindow: vi.fn(),
      setDraftConversationHistoryLength: vi.fn(),
      setDraftLanguage: vi.fn(),
      setDraftTheme: vi.fn(),
      commitDraft: vi.fn(),
      discardDraft: vi.fn(),
      reset: vi.fn(),
    }),
  selectContextWindow: (s: { contextWindowTokens: number }) => s.contextWindowTokens,
  selectConversationHistoryLength: (s: { conversationHistoryLength: number }) =>
    s.conversationHistoryLength,
  selectLanguage: (s: { language: string }) => s.language,
  selectTheme: (s: { theme: string }) => s.theme,
  selectSetContextWindow: (s: { setContextWindow: unknown }) => s.setContextWindow,
  selectSetConversationHistoryLength: (s: { setConversationHistoryLength: unknown }) =>
    s.setConversationHistoryLength,
  selectSetLanguage: (s: { setLanguage: unknown }) => s.setLanguage,
  selectSetTheme: (s: { setTheme: unknown }) => s.setTheme,
  selectResetSettings: (s: { reset: unknown }) => s.reset,
  // draft selectors (required by SettingsSaveFooter mounted inside SettingsPanel)
  selectDraftContextWindow: (s: { draftContextWindowTokens: number }) => s.draftContextWindowTokens,
  selectDraftConversationHistoryLength: (s: { draftConversationHistoryLength: number }) =>
    s.draftConversationHistoryLength,
  selectDraftLanguage: (s: { draftLanguage: string }) => s.draftLanguage,
  selectDraftTheme: (s: { draftTheme: string }) => s.draftTheme,
  selectSetDraftContextWindow: (s: { setDraftContextWindow: unknown }) => s.setDraftContextWindow,
  selectSetDraftConversationHistoryLength: (s: { setDraftConversationHistoryLength: unknown }) =>
    s.setDraftConversationHistoryLength,
  selectSetDraftLanguage: (s: { setDraftLanguage: unknown }) => s.setDraftLanguage,
  selectSetDraftTheme: (s: { setDraftTheme: unknown }) => s.setDraftTheme,
  selectCommitDraft: (s: { commitDraft: unknown }) => s.commitDraft,
  selectDiscardDraft: (s: { discardDraft: unknown }) => s.discardDraft,
  selectIsDirty: (s: {
    draftTheme: string; theme: string;
    draftLanguage: string; language: string;
    draftConversationHistoryLength: number; conversationHistoryLength: number;
    draftContextWindowTokens: number; contextWindowTokens: number;
  }) =>
    s.draftTheme !== s.theme ||
    s.draftLanguage !== s.language ||
    s.draftConversationHistoryLength !== s.conversationHistoryLength ||
    s.draftContextWindowTokens !== s.contextWindowTokens,
  CONTEXT_WINDOW_OPTIONS: [4096, 8192, 32768],
  CONV_HISTORY_OPTIONS: [2, 4, 10],
  computeBudgetSplit: () => ({ history: 0, retrieved: 0, system: 0, generation: 0 }),
  formatTokenCount: (n: number) => `${n}`,
}));

// ─── Mock providerStore ───────────────────────────────────────────────────────

vi.mock("../store/providerStore", () => ({
  useProviderStore: (selector: (s: unknown) => unknown) =>
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
  useShallow: (fn: unknown) => fn,
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

// ─── Mock graphStore ──────────────────────────────────────────────────────────

vi.mock("../store/graphStore", () => ({
  useGraphStore: (selector: (s: unknown) => unknown) =>
    selector({ vaultId: "vault-1" }),
  selectVaultId: (s: { vaultId: string }) => s.vaultId,
}));

// ─── Mock zustand shallow ─────────────────────────────────────────────────────

vi.mock("zustand/react/shallow", () => ({
  useShallow: (fn: unknown) => fn,
}));

// ─── Mock ImportScheduleCard ──────────────────────────────────────────────────

vi.mock("../components/settings/ImportScheduleCard", () => ({
  ImportScheduleCard: () => <div data-testid="import-schedule-card" />,
}));

// ─── Mock providerClient (minimal — tests focus on costsClient) ───────────────

vi.mock("../api/providerClient", async (importOriginal) => {
  const orig = await importOriginal<typeof import("../api/providerClient")>();
  return {
    ...orig,
    fetchEmbeddingConfig: vi.fn().mockResolvedValue({
      embedding_url: "http://localhost:11434/api/embeddings",
      embedding_model: "bge-m3",
      embedding_dim: 1024,
      embeddings_enabled: true,
    }),
    fetchMcpInfo: vi.fn().mockResolvedValue({
      server_name: "synapse",
      transport: "stdio",
      entry_point_command: "python -m app.mcp.server",
      tool_count: 0,
      tools: [],
      http_enabled: false,
      remote_write_enabled: false,
      token_configured: false,
      remote_enabled: false,
      mount_path: "/mcp/server",
      token_source: "none",
      allow_without_token: false,
    }),
    setRemoteMcpEnabled: vi.fn().mockResolvedValue({ remote_enabled: false, token_configured: false, mount_path: "/mcp/server", clamped: false }),
    setMcpAuth: vi.fn().mockResolvedValue({ token_configured: false, token_source: "none", allow_without_token: false, remote_enabled: false, mount_path: "/mcp/server" }),
    fetchClipConfig: vi.fn().mockResolvedValue({ enabled: false, token_configured: false, token_source: "none", allowed_origins: [], max_body_bytes: 1048576 }),
    setClipConfig: vi.fn().mockResolvedValue({ enabled: false, token_configured: false, token_source: "none", allowed_origins: [], max_body_bytes: 1048576 }),
    fetchWebSearchConfig: vi.fn().mockResolvedValue({ configured: false, url: "", categories: [], max_queries: 3, source: "none" }),
    setWebSearchConfig: vi.fn().mockResolvedValue({ configured: false, url: "", categories: [], max_queries: 3, source: "none" }),
    getCliAuthConfig: vi.fn().mockResolvedValue({ token_configured: false, token_source: "none", auth_mode: "unconfigured" }),
    setCliAuthConfig: vi.fn().mockResolvedValue({ token_configured: false, token_source: "none", auth_mode: "unconfigured" }),
    fetchVendors: vi.fn().mockResolvedValue({ vendors: [] }),
    fetchProviderConfigs: vi.fn().mockResolvedValue({ items: [], total: 0 }),
    createProviderConfig: vi.fn(),
    updateProviderConfig: vi.fn(),
    deleteProviderConfig: vi.fn(),
    testProviderConnection: vi.fn(),
    testProviderFunction: vi.fn(),
  };
});

// ─── Mock scenariosClient ─────────────────────────────────────────────────────

vi.mock("../api/scenariosClient", () => ({
  fetchScenarios: vi.fn().mockResolvedValue([]),
  applyScenario: vi.fn().mockResolvedValue({ applied: true }),
}));

// ─── Mock appConfigClient (R11-2) ────────────────────────────────────────────

vi.mock("../api/appConfigClient", () => ({
  getAppConfig: vi.fn().mockResolvedValue({
    settings: [
      { key: "pdf_extractor",           value: "pypdf",  source: "env" },
      { key: "marker_service_url",       value: "",       source: "env" },
      { key: "marker_timeout_seconds",   value: "60",     source: "env" },
      { key: "cost_alert_threshold_usd", value: "5.0",    source: "env" },
      { key: "embeddings_enabled",       value: "true",   source: "env" },
      { key: "embedding_format",         value: "ollama", source: "env" },
      { key: "overview_language",        value: "en",     source: "env" },
      { key: "wikilink_enrich_enabled",  value: "true",   source: "env" },
    ],
  }),
  putAppConfig: vi.fn().mockResolvedValue(undefined),
  resetAppConfig: vi.fn().mockResolvedValue(undefined),
}));

// ─── costsClient mock ─────────────────────────────────────────────────────────

import type { CostsSummary } from "../api/costsClient";

const BASE_COSTS: CostsSummary = {
  period: "2026-07",
  by_provider: [
    { provider: "api/anthropic", total_usd: 1.23, call_count: 42 },
  ],
  by_provider_note: null,
  by_operation: [
    { operation: "ingest", total_usd: 0.75, call_count: 30 },
    { operation: "chat", total_usd: 0.48, call_count: 12 },
  ],
  by_day: [
    { date: "2026-07-01", total_usd: 0.12 },
    { date: "2026-07-02", total_usd: 0.45 },
  ],
  monthly_total_usd: 1.23,
  threshold_usd: 5.0,
  threshold_alert: false,
};

const mockFetchCostsSummary = vi.fn().mockResolvedValue(BASE_COSTS);

vi.mock("../api/costsClient", () => ({
  fetchCostsSummary: (...args: unknown[]) => mockFetchCostsSummary(...args),
}));

// ─── Helper ───────────────────────────────────────────────────────────────────

function navigateToCosts() {
  render(<SettingsPanel />);
  // ADR-0055: SectionCosts is on page "costs" in the 2-level nav.
  const costsBtn = document.querySelector('[data-settings-section="costs"]');
  if (!costsBtn) throw new Error("costs page nav button not found in rendered SettingsPanel");
  fireEvent.click(costsBtn);
}

beforeEach(() => {
  vi.clearAllMocks();
  mockFetchCostsSummary.mockResolvedValue(BASE_COSTS);
});

// ─── A. Nav group renders ─────────────────────────────────────────────────────
// ADR-0055: SectionCosts is now on page "costs" in the 2-level nav.

describe("SettingsPanel — costs nav item", () => {
  it("renders the 'costs' page button (ADR-0055)", () => {
    render(<SettingsPanel />);
    const btn = document.querySelector('[data-settings-section="costs"]');
    expect(btn).not.toBeNull();
  });

  it("clicking costs nav shows the costs section (loading state initially)", () => {
    navigateToCosts();
    // While fetch is pending, loading text appears
    expect(screen.getAllByText("loading").length).toBeGreaterThanOrEqual(1);
  });
});

// ─── B. Monthly total ─────────────────────────────────────────────────────────

describe("SectionCosts — monthly total display (AC-R9-1-2)", () => {
  it("renders costs-monthly-total after fetch resolves", async () => {
    navigateToCosts();
    await waitFor(() => {
      expect(document.querySelector('[data-testid="costs-monthly-total"]')).not.toBeNull();
    });
    const el = document.querySelector('[data-testid="costs-monthly-total"]');
    expect(el?.textContent).toContain("1.23");
  });
});

// ─── C. Threshold alert shown ─────────────────────────────────────────────────

describe("SectionCosts — threshold alert badge (AC-R9-1-3)", () => {
  beforeEach(() => {
    mockFetchCostsSummary.mockResolvedValue({
      ...BASE_COSTS,
      monthly_total_usd: 8.5,
      threshold_usd: 5.0,
      threshold_alert: true,
    });
  });

  it("shows costs-threshold-alert when threshold_alert=true", async () => {
    navigateToCosts();
    await waitFor(() => {
      expect(document.querySelector('[data-testid="costs-threshold-alert"]')).not.toBeNull();
    });
  });

  it("threshold alert has role=alert", async () => {
    navigateToCosts();
    await waitFor(() => {
      const el = document.querySelector('[data-testid="costs-threshold-alert"]');
      expect(el?.getAttribute("role")).toBe("alert");
    });
  });
});

// ─── D. No threshold alert when false ────────────────────────────────────────

describe("SectionCosts — no threshold alert when threshold_alert=false (AC-R9-1-3)", () => {
  it("does NOT render costs-threshold-alert when threshold_alert=false", async () => {
    navigateToCosts();
    await waitFor(() => {
      expect(document.querySelector('[data-testid="costs-monthly-total"]')).not.toBeNull();
    });
    // threshold_alert is false in BASE_COSTS
    expect(document.querySelector('[data-testid="costs-threshold-alert"]')).toBeNull();
  });
});

// ─── E. SVG bar chart ────────────────────────────────────────────────────────

describe("SectionCosts — SVG bar chart (I9: no chart library)", () => {
  it("renders costs-day-chart as an SVG element after fetch resolves", async () => {
    navigateToCosts();
    await waitFor(() => {
      expect(document.querySelector('[data-testid="costs-day-chart"]')).not.toBeNull();
    });
    const chart = document.querySelector('[data-testid="costs-day-chart"]');
    // Must be SVG (not a canvas or third-party library element)
    expect(chart?.tagName.toLowerCase()).toBe("svg");
  });
});

// ─── F. by_provider table ────────────────────────────────────────────────────

describe("SectionCosts — by_provider table (AC-R9-1-4)", () => {
  it("renders costs-by-provider table after fetch resolves", async () => {
    navigateToCosts();
    await waitFor(() => {
      expect(document.querySelector('[data-testid="costs-by-provider"]')).not.toBeNull();
    });
    // Provider name should appear in the table
    const el = document.querySelector('[data-testid="costs-by-provider"]');
    expect(el?.textContent).toContain("api/anthropic");
  });
});

// ─── G. by_operation table ───────────────────────────────────────────────────

describe("SectionCosts — by_operation table (AC-R9-1-5)", () => {
  it("renders costs-by-operation table after fetch resolves", async () => {
    navigateToCosts();
    await waitFor(() => {
      expect(document.querySelector('[data-testid="costs-by-operation"]')).not.toBeNull();
    });
    const el = document.querySelector('[data-testid="costs-by-operation"]');
    expect(el?.textContent).toContain("ingest");
    expect(el?.textContent).toContain("chat");
  });
});

// ─── H. Loading state ─────────────────────────────────────────────────────────

describe("SectionCosts — loading state (AC-R9-1-1)", () => {
  it("shows loading text before fetch resolves", () => {
    // Keep fetch pending by never resolving
    mockFetchCostsSummary.mockReturnValue(new Promise(() => {}));
    navigateToCosts();
    expect(screen.getAllByText("loading").length).toBeGreaterThanOrEqual(1);
    // Monthly total not yet shown
    expect(document.querySelector('[data-testid="costs-monthly-total"]')).toBeNull();
  });
});

// ─── I. Error state ───────────────────────────────────────────────────────────

describe("SectionCosts — error state", () => {
  it("shows error text when fetchCostsSummary rejects", async () => {
    mockFetchCostsSummary.mockRejectedValue(new Error("network error"));
    navigateToCosts();
    await waitFor(() => {
      expect(screen.getByText("error")).toBeTruthy();
    });
    // No monthly total
    expect(document.querySelector('[data-testid="costs-monthly-total"]')).toBeNull();
  });
});
