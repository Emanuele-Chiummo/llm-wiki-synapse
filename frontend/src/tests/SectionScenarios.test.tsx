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
      <button data-testid="confirm-dialog-confirm" onClick={onConfirm}>{confirmLabel}</button>
      <button data-testid="confirm-dialog-cancel" onClick={onCancel}>{cancelLabel}</button>
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
      language: "en",
      conversationHistoryLength: 20,
      theme: "system",
      setContextWindow: vi.fn(),
      setLanguage: vi.fn(),
      setConversationHistoryLength: vi.fn(),
      resetSettings: vi.fn(),
      setTheme: vi.fn(),
    }),
  ),
  selectContextWindow: (s: { contextWindow: number }) => s.contextWindow,
  selectLanguage: (s: { language: string }) => s.language,
  selectConversationHistoryLength: (s: { conversationHistoryLength: number }) => s.conversationHistoryLength,
  selectSetContextWindow: (s: { setContextWindow: () => void }) => s.setContextWindow,
  selectSetLanguage: (s: { setLanguage: () => void }) => s.setLanguage,
  selectSetConversationHistoryLength: (s: { setConversationHistoryLength: () => void }) => s.setConversationHistoryLength,
  selectResetSettings: (s: { resetSettings: () => void }) => s.resetSettings,
  selectTheme: (s: { theme: string }) => s.theme,
  selectSetTheme: (s: { setTheme: () => void }) => s.setTheme,
  CONTEXT_WINDOW_OPTIONS: [4096, 32768, 131072],
  CONV_HISTORY_OPTIONS: [10, 20, 50],
  computeBudgetSplit: () => ({ history: 100, retrieved: 50, system: 20, generation: 60 }),
  formatTokenCount: (n: number) => `${n}`,
}));

vi.mock("../store/providerStore", () => ({
  useProviderStore: vi.fn((selector: (s: unknown) => unknown) =>
    selector({
      providerList: [],
      providerLoading: false,
      providerError: null,
      fetchProviderList: vi.fn(),
      addProvider: vi.fn(),
      deleteProvider: vi.fn(),
    }),
  ),
  selectProviderList: (s: { providerList: unknown[] }) => s.providerList,
  selectProviderLoading: (s: { providerLoading: boolean }) => s.providerLoading,
  selectProviderError: (s: { providerError: string | null }) => s.providerError,
  selectFetchProviderList: (s: { fetchProviderList: () => void }) => s.fetchProviderList,
  selectAddProvider: (s: { addProvider: () => void }) => s.addProvider,
  selectDeleteProvider: (s: { deleteProvider: () => void }) => s.deleteProvider,
}));

vi.mock("../store/graphStore", () => ({
  useGraphStore: vi.fn((selector: (s: unknown) => unknown) =>
    selector({ vaultId: "v1" }),
  ),
  selectVaultId: (s: { vaultId: string }) => s.vaultId,
}));

vi.mock("../api/providerClient", () => ({
  fetchEmbeddingConfig: vi.fn(),
  fetchMcpInfo: vi.fn(),
  setRemoteMcpEnabled: vi.fn(),
  setMcpAuth: vi.fn(),
  fetchClipConfig: vi.fn(),
  setClipConfig: vi.fn(),
  fetchWebSearchConfig: vi.fn(),
  setWebSearchConfig: vi.fn(),
  getCliAuthConfig: vi.fn(),
  setCliAuthConfig: vi.fn(),
}));

vi.mock("../components/settings/ImportScheduleCard", () => ({
  ImportScheduleCard: () => <div data-testid="import-schedule-card" />,
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
        // Nav items needed by SettingsPanel render
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
  await waitFor(() => {
    // Nav item should be present
    expect(screen.getAllByText("Scenarios").length).toBeGreaterThan(0);
  });
  // Click the Scenarios nav item
  const btns = screen.getAllByText("Scenarios");
  fireEvent.click(btns[0]!);
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe("SectionScenarios — R7-1 (FE)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows loading while fetchScenarios is pending", async () => {
    vi.mocked(scenariosClient.fetchScenarios).mockReturnValue(
      new Promise(() => { /* never resolves */ }),
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
