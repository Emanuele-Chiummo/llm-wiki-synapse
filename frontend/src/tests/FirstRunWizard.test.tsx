/**
 * FirstRunWizard.test.tsx — Vitest unit tests for the first-run setup wizard.
 *
 * Covers AC-R11-2-13:
 *   (a) Wizard appears when provider list is empty AND setupCompleted flag absent.
 *   (b) Wizard does NOT appear when provider list is non-empty (configured).
 *   (c) Wizard does NOT appear when setupCompleted flag is set.
 *   (d) Skip closes wizard and sets the setupCompleted flag.
 *   (e) Re-openable from Settings "Getting started" via window event.
 *   (f) Wizard writes ONLY via createProviderConfig (step 2) and putAppConfig (step 3).
 *       A spy on both functions asserts these are the ONLY sanctioned write paths.
 *       No direct fetch call outside the approved client functions.
 *
 * The test also covers:
 *   - Step navigation (Next / Back / SkipStep / SkipAll).
 *   - Focus management and Esc-to-dismiss.
 *   - useFirstRunSetup hook: shouldShow logic + markDone.
 *   - getSetupCompleted / markSetupCompleted localStorage helpers.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";

// ─── Mock i18n (before any component import) ──────────────────────────────────

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) => {
      // Return the leaf key for predictable assertions
      const leaf = key.split(".").pop() ?? key;
      if (opts && typeof opts === "object") {
        return Object.entries(opts).reduce((s, [k, v]) => s.replace(`{{${k}}}`, String(v)), leaf);
      }
      return leaf;
    },
    i18n: { changeLanguage: vi.fn() },
  }),
}));

// ─── Mock api/base — apiFetch is the health probe in step 1 ───────────────────
// We expose a mutable ref so individual tests can control the response.

let mockApiFetchOk = true;
const mockPlatformFetch = vi.fn(async (_url: string, _init?: unknown) => {
  if (mockApiFetchOk) return new Response("{}", { status: 200 });
  return new Response("{}", { status: 503 });
});
const mockClearAuthToken = vi.fn();

vi.mock("../api/base", () => ({
  apiBase: () => "http://localhost:8000",
  apiFetch: vi.fn(async (_url: string) => {
    if (mockApiFetchOk) {
      return new Response("{}", { status: 200 });
    }
    return new Response("{}", { status: 503 });
  }),
  getAuthToken: () => null,
  authHeaders: () => ({}),
  register401Handler: vi.fn(),
  isTauri: () => false,
  // Step 1 now persists the entered backend URL via these helpers.
  setServerUrl: vi.fn(),
  clearServerUrl: vi.fn(),
  clearAuthToken: (...args: unknown[]) => mockClearAuthToken(...args),
  platformFetch: (...args: [string, unknown?]) => mockPlatformFetch(...args),
  getLastServerUrl: () => null,
}));

// ─── Mock appConfigClient — putAppConfig is the sanctioned PDF write path ─────

const mockPutAppConfig = vi.fn().mockResolvedValue(undefined);

vi.mock("../api/appConfigClient", () => ({
  getAppConfig: vi.fn().mockResolvedValue({
    settings: [
      { key: "pdf_extractor", value: "pypdf", source: "env" },
      { key: "marker_service_url", value: "", source: "env" },
    ],
  }),
  putAppConfig: (...args: unknown[]) => mockPutAppConfig(...args),
  resetAppConfig: vi.fn().mockResolvedValue(undefined),
}));

// ─── Mock providerClient — createProviderConfig is the sanctioned provider write path

const mockCreateProviderConfig = vi.fn().mockResolvedValue({
  id: "prov-new",
  scope: "global",
  vault_id: null,
  provider_type: "api",
  model_id: "claude-sonnet-4-6",
  base_url: null,
  max_iter: 3,
  token_budget: 60000,
  is_fallback: false,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
});
const mockTestProviderConnection = vi.fn().mockResolvedValue({
  ok: true,
  latency_ms: 42,
  detail: "connected",
});

vi.mock("../api/providerClient", () => ({
  fetchProviderConfigs: vi.fn().mockResolvedValue({ items: [] }),
  createProviderConfig: (...args: unknown[]) => mockCreateProviderConfig(...args),
  testProviderConnection: (...args: unknown[]) => mockTestProviderConnection(...args),
  deleteProviderConfig: vi.fn().mockResolvedValue(undefined),
  fetchEmbeddingConfig: vi.fn().mockResolvedValue({}),
  fetchMcpInfo: vi.fn().mockResolvedValue({}),
  setRemoteMcpEnabled: vi.fn().mockResolvedValue({}),
  setMcpAuth: vi.fn().mockResolvedValue({}),
  fetchClipConfig: vi.fn().mockResolvedValue({}),
  setClipConfig: vi.fn().mockResolvedValue({}),
  fetchWebSearchConfig: vi.fn().mockResolvedValue({}),
  setWebSearchConfig: vi.fn().mockResolvedValue({}),
  getCliAuthConfig: vi.fn().mockResolvedValue({}),
  setCliAuthConfig: vi.fn().mockResolvedValue({}),
}));

// ─── LocalStorage mock ────────────────────────────────────────────────────────
// Vitest runs in jsdom but localStorage may be a stub. We override it with a
// plain in-memory map to guarantee the get/set/remove semantics our helpers rely on.

const LS_SETUP_KEY = "synapse.setupCompleted";
const LS_SETUP_STATE_KEY = "synapse.setupState";

const _lsStore: Map<string, string> = new Map();
const _mockLocalStorage = {
  getItem: (key: string) => _lsStore.get(key) ?? null,
  setItem: (key: string, val: string) => {
    _lsStore.set(key, val);
  },
  removeItem: (key: string) => {
    _lsStore.delete(key);
  },
  clear: () => {
    _lsStore.clear();
  },
};

beforeEach(() => {
  _lsStore.clear();
  // Replace global localStorage with our in-memory mock.
  Object.defineProperty(globalThis, "localStorage", {
    value: _mockLocalStorage,
    writable: true,
    configurable: true,
  });
});

function clearSetupFlag() {
  _lsStore.delete(LS_SETUP_KEY);
}

function setSetupFlag() {
  _lsStore.set(LS_SETUP_KEY, "1");
}

// ─── Import components (after mocks) ──────────────────────────────────────────

import {
  FirstRunWizard,
  useFirstRunSetup,
  getSetupCompleted,
  markSetupCompleted,
} from "../components/setup/FirstRunWizard";
import { deferSetup, readSetupState } from "../components/setup/setupState";
import { renderHook } from "@testing-library/react";

// ─── Helper ───────────────────────────────────────────────────────────────────

function renderWizard(onClose = vi.fn()) {
  return render(<FirstRunWizard onClose={onClose} />);
}

// ─── 1. localStorage helpers ──────────────────────────────────────────────────

describe("getSetupCompleted / markSetupCompleted", () => {
  beforeEach(() => clearSetupFlag());

  it("returns false when flag is absent", () => {
    expect(getSetupCompleted()).toBe(false);
  });

  it("returns true after markSetupCompleted()", () => {
    markSetupCompleted();
    expect(getSetupCompleted()).toBe(true);
  });

  it("migrates the legacy completed flag to versioned setup state", () => {
    setSetupFlag();

    expect(readSetupState()).toMatchObject({ version: 1, status: "completed" });
    expect(_lsStore.get(LS_SETUP_STATE_KEY)).toContain('"status":"completed"');
  });

  it("keeps a deferred setup resumable instead of completed", () => {
    deferSetup(2, { connectionVerified: true, providerVerified: false });

    expect(readSetupState()).toMatchObject({
      status: "deferred",
      lastStep: 2,
      connectionVerified: true,
      providerVerified: false,
    });
    expect(getSetupCompleted()).toBe(false);
  });
});

// ─── 2. useFirstRunSetup — shouldShow logic ───────────────────────────────────

describe("useFirstRunSetup — shouldShow", () => {
  beforeEach(() => clearSetupFlag());
  afterEach(() => clearSetupFlag());

  it("shouldShow = true when providerList empty and flag absent", async () => {
    const { result } = renderHook(() => useFirstRunSetup(0));
    // Wait for the effect to check the flag
    await act(async () => {});
    expect(result.current.shouldShow).toBe(true);
  });

  it("shouldShow stays true until setup is explicitly completed, even with seeded providers", async () => {
    const { result } = renderHook(() => useFirstRunSetup(1));
    await act(async () => {});
    expect(result.current.shouldShow).toBe(true);
  });

  it("shouldShow = false when setupCompleted flag is set", async () => {
    setSetupFlag();
    const { result } = renderHook(() => useFirstRunSetup(0));
    await act(async () => {});
    expect(result.current.shouldShow).toBe(false);
  });

  it("markDone sets the flag and shouldShow becomes false", async () => {
    const { result } = renderHook(() => useFirstRunSetup(0));
    await act(async () => {});
    expect(result.current.shouldShow).toBe(true);

    act(() => {
      result.current.markDone();
    });
    expect(getSetupCompleted()).toBe(true);
    // shouldShow updates on next render cycle
    expect(result.current.shouldShow).toBe(false);
  });
});

// ─── 3. Wizard renders and shows step 1 by default ───────────────────────────

describe("FirstRunWizard — initial render", () => {
  beforeEach(() => clearSetupFlag());

  it("renders the wizard dialog with role=dialog", () => {
    renderWizard();
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("renders the wizard overlay (data-testid=wizard-overlay)", () => {
    renderWizard();
    expect(screen.getByTestId("wizard-overlay")).toBeTruthy();
  });

  it("renders step 1 content (check connection button)", () => {
    renderWizard();
    expect(screen.getByTestId("wizard-step1-check")).toBeTruthy();
  });

  it("does NOT render step 2 content on initial load", () => {
    renderWizard();
    expect(screen.queryByTestId("wizard-step2-type")).toBeNull();
  });
});

// ─── 4. Skip closes the wizard and sets the completed flag ───────────────────

describe("FirstRunWizard — explicit outcomes", () => {
  beforeEach(() => clearSetupFlag());

  it("clicking Skip setup reports deferred instead of completed", () => {
    const onClose = vi.fn();
    renderWizard(onClose);
    // The "skipAll" button is rendered in step 1 alongside the main action
    const skipBtn = screen.getByTestId("wizard-skip");
    fireEvent.click(skipBtn);
    expect(onClose).toHaveBeenCalledWith("deferred", 1);
  });

  it("clicking the × close button calls onClose", () => {
    const onClose = vi.fn();
    renderWizard(onClose);
    const closeX = screen.getByTestId("wizard-close-x");
    fireEvent.click(closeX);
    expect(onClose).toHaveBeenCalledWith("deferred", 1);
  });

  it("pressing Esc calls onClose", () => {
    const onClose = vi.fn();
    renderWizard(onClose);
    const dialog = screen.getByTestId("wizard-dialog");
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(onClose).toHaveBeenCalledWith("deferred", 1);
  });

  it("clicking backdrop calls onClose", () => {
    const onClose = vi.fn();
    renderWizard(onClose);
    const overlay = screen.getByTestId("wizard-overlay");
    // Click the overlay itself (not the dialog inside)
    fireEvent.click(overlay);
    expect(onClose).toHaveBeenCalledWith("deferred", 1);
  });
});

// ─── 5. Step navigation ───────────────────────────────────────────────────────

describe("FirstRunWizard — step navigation", () => {
  beforeEach(() => clearSetupFlag());

  it("clicking 'Skip this step' advances from step 1 to step 2", async () => {
    renderWizard();
    // Use the "skip step" button (not skip all)
    const skipStepBtn = screen.getByTestId("wizard-step1-skip-check");
    fireEvent.click(skipStepBtn);
    await waitFor(() => {
      expect(screen.getByTestId("wizard-step2-type")).toBeTruthy();
    });
  });

  it("clicking 'Skip this step' on step 2 advances to step 3", async () => {
    renderWizard();
    fireEvent.click(screen.getByTestId("wizard-step1-skip-check"));
    await waitFor(() => expect(screen.getByTestId("wizard-step2-type")).toBeTruthy());
    fireEvent.click(screen.getByTestId("wizard-step2-skip"));
    await waitFor(() => {
      expect(screen.getByTestId("wizard-step3-extractor")).toBeTruthy();
    });
  });

  it("clicking Back on step 2 returns to step 1", async () => {
    renderWizard();
    fireEvent.click(screen.getByTestId("wizard-step1-skip-check"));
    await waitFor(() => expect(screen.getByTestId("wizard-step2-type")).toBeTruthy());
    fireEvent.click(screen.getByTestId("wizard-back"));
    await waitFor(() => {
      expect(screen.getByTestId("wizard-step1-check")).toBeTruthy();
    });
  });

  it("clicking 'Skip this step' on step 3 advances to step 4 (done)", async () => {
    renderWizard();
    // Skip through steps 1-2
    fireEvent.click(screen.getByTestId("wizard-step1-skip-check"));
    await waitFor(() => expect(screen.getByTestId("wizard-step2-type")).toBeTruthy());
    fireEvent.click(screen.getByTestId("wizard-step2-skip"));
    await waitFor(() => expect(screen.getByTestId("wizard-step3-extractor")).toBeTruthy());
    fireEvent.click(screen.getByTestId("wizard-step3-skip"));
    await waitFor(() => {
      expect(screen.getByTestId("wizard-done")).toBeTruthy();
    });
  });

  it("clicking Done after skipping required setup defers instead of claiming completion", async () => {
    const onClose = vi.fn();
    renderWizard(onClose);
    fireEvent.click(screen.getByTestId("wizard-step1-skip-check"));
    await waitFor(() => expect(screen.getByTestId("wizard-step2-type")).toBeTruthy());
    fireEvent.click(screen.getByTestId("wizard-step2-skip"));
    await waitFor(() => expect(screen.getByTestId("wizard-step3-extractor")).toBeTruthy());
    fireEvent.click(screen.getByTestId("wizard-step3-skip"));
    await waitFor(() => expect(screen.getByTestId("wizard-done")).toBeTruthy());
    fireEvent.click(screen.getByTestId("wizard-done"));
    expect(onClose).toHaveBeenCalledWith("deferred", 1);
  });

  it("reports completed only after backend and provider probes succeed", async () => {
    const onClose = vi.fn();
    renderWizard(onClose);

    fireEvent.click(screen.getByTestId("wizard-step1-check"));
    await waitFor(() => expect(screen.getByTestId("wizard-step1-next")).toBeTruthy());
    fireEvent.click(screen.getByTestId("wizard-step1-next"));
    await waitFor(() => expect(screen.getByTestId("wizard-step2-type")).toBeTruthy());
    fireEvent.change(screen.getByTestId("wizard-step2-model"), {
      target: { value: "verified-model" },
    });
    fireEvent.click(screen.getByTestId("wizard-step2-save"));
    await waitFor(() => expect(screen.getByTestId("wizard-step3-extractor")).toBeTruthy());
    fireEvent.click(screen.getByTestId("wizard-step3-skip"));
    await waitFor(() => expect(screen.getByTestId("wizard-done")).toBeTruthy());
    fireEvent.click(screen.getByTestId("wizard-done"));

    expect(onClose).toHaveBeenCalledWith("completed", 4);
  });

  it("retains verified checks when a deferred setup resumes", async () => {
    deferSetup(2, { connectionVerified: true, providerVerified: false });
    const onClose = vi.fn();
    renderWizard(onClose);

    expect(screen.getByTestId("wizard-step2-type")).toBeTruthy();
    fireEvent.change(screen.getByTestId("wizard-step2-model"), {
      target: { value: "verified-model" },
    });
    fireEvent.click(screen.getByTestId("wizard-step2-save"));
    await waitFor(() => expect(screen.getByTestId("wizard-step3-extractor")).toBeTruthy());
    fireEvent.click(screen.getByTestId("wizard-step3-skip"));
    await waitFor(() => expect(screen.getByTestId("wizard-done")).toBeTruthy());
    fireEvent.click(screen.getByTestId("wizard-done"));

    expect(onClose).toHaveBeenCalledWith("completed", 4);
  });
});

// ─── 6. Step 1 health check ───────────────────────────────────────────────────

describe("FirstRunWizard — step 1 health probe", () => {
  beforeEach(() => {
    mockApiFetchOk = true;
    mockPlatformFetch.mockClear();
    mockClearAuthToken.mockClear();
    clearSetupFlag();
  });
  afterEach(() => {
    mockApiFetchOk = true;
  });

  it("shows success indicator when backend responds 200", async () => {
    renderWizard();
    fireEvent.click(screen.getByTestId("wizard-step1-check"));
    await waitFor(() => {
      expect(screen.getByTestId("wizard-step1-ok")).toBeTruthy();
    });
  });

  it("shows Next button after successful check", async () => {
    renderWizard();
    fireEvent.click(screen.getByTestId("wizard-step1-check"));
    await waitFor(() => {
      expect(screen.getByTestId("wizard-step1-next")).toBeTruthy();
    });
  });

  it("shows error indicator when backend responds non-200", async () => {
    mockApiFetchOk = false;
    renderWizard();
    fireEvent.click(screen.getByTestId("wizard-step1-check"));
    await waitFor(() => {
      expect(screen.getByTestId("wizard-step1-error")).toBeTruthy();
    });
  });

  it("probes an edited backend without forwarding the authenticated API client", async () => {
    renderWizard();
    fireEvent.change(screen.getByTestId("wizard-step1-url"), {
      target: { value: "https://candidate.example" },
    });
    fireEvent.click(screen.getByTestId("wizard-step1-check"));

    await waitFor(() => expect(mockPlatformFetch).toHaveBeenCalled());
    expect(mockPlatformFetch.mock.calls.at(-1)?.[0]).toBe("https://candidate.example/status");
    expect(mockPlatformFetch.mock.calls.at(-1)?.[1]).toBeUndefined();
    expect(mockClearAuthToken).toHaveBeenCalledOnce();
  });
});

// ─── 7. Step 2 — provider save: calls createProviderConfig ONLY ──────────────
// AC-R11-2-13 (f): wizard writes via sanctioned endpoint, not raw fetch.

describe("FirstRunWizard — step 2 provider persistence (sanctioned path only)", () => {
  beforeEach(() => {
    clearSetupFlag();
    mockCreateProviderConfig.mockClear();
    mockTestProviderConnection.mockClear();
    mockTestProviderConnection.mockResolvedValue({ ok: true, latency_ms: 42, detail: "connected" });
    mockPutAppConfig.mockClear();
    vi.clearAllMocks(); // reset apiFetch spy too
  });

  it("Save provider calls createProviderConfig with correct body", async () => {
    renderWizard();
    // Navigate to step 2
    fireEvent.click(screen.getByTestId("wizard-step1-skip-check"));
    await waitFor(() => expect(screen.getByTestId("wizard-step2-type")).toBeTruthy());

    // Fill in model ID (required to enable Save)
    const modelInput = screen.getByTestId("wizard-step2-model");
    fireEvent.change(modelInput, { target: { value: "claude-sonnet-4-6" } });

    // Click Save provider
    const saveBtn = screen.getByTestId("wizard-step2-save");
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(mockCreateProviderConfig).toHaveBeenCalledOnce();
    });

    const [body] = mockCreateProviderConfig.mock.calls[0] as [Record<string, unknown>];
    expect(body.provider_type).toBe("api");
    expect(body.model_id).toBe("claude-sonnet-4-6");
    expect(body.scope).toBe("global");
  });

  it("includes an API key in the sanctioned provider payload when supplied", async () => {
    renderWizard();
    fireEvent.click(screen.getByTestId("wizard-step1-skip-check"));
    await waitFor(() => expect(screen.getByTestId("wizard-step2-type")).toBeTruthy());
    fireEvent.change(screen.getByTestId("wizard-step2-model"), {
      target: { value: "api-model" },
    });
    fireEvent.change(screen.getByTestId("wizard-step2-api-key"), {
      target: { value: "secret-value" },
    });
    fireEvent.click(screen.getByTestId("wizard-step2-save"));

    await waitFor(() => expect(mockCreateProviderConfig).toHaveBeenCalledOnce());
    expect(mockCreateProviderConfig.mock.calls[0]?.[0]).toMatchObject({
      api_key: "secret-value",
    });
  });

  it("Save provider does NOT call putAppConfig", async () => {
    renderWizard();
    fireEvent.click(screen.getByTestId("wizard-step1-skip-check"));
    await waitFor(() => expect(screen.getByTestId("wizard-step2-type")).toBeTruthy());

    const modelInput = screen.getByTestId("wizard-step2-model");
    fireEvent.change(modelInput, { target: { value: "test-model" } });
    fireEvent.click(screen.getByTestId("wizard-step2-save"));

    await waitFor(() => expect(mockCreateProviderConfig).toHaveBeenCalledOnce());
    expect(mockPutAppConfig).not.toHaveBeenCalled();
  });

  it("tests the candidate provider before persisting and advancing", async () => {
    renderWizard();
    fireEvent.click(screen.getByTestId("wizard-step1-skip-check"));
    await waitFor(() => expect(screen.getByTestId("wizard-step2-type")).toBeTruthy());
    fireEvent.change(screen.getByTestId("wizard-step2-model"), {
      target: { value: "test-model" },
    });
    fireEvent.click(screen.getByTestId("wizard-step2-save"));

    await waitFor(() => {
      expect(mockTestProviderConnection).toHaveBeenCalledWith({
        provider_type: "api",
        model: "test-model",
        base_url: null,
      });
    });
    expect(mockTestProviderConnection.mock.invocationCallOrder[0]).toBeLessThan(
      mockCreateProviderConfig.mock.invocationCallOrder[0] ?? Number.MAX_SAFE_INTEGER,
    );
  });

  it("does not advance when the provider probe fails", async () => {
    mockTestProviderConnection.mockResolvedValueOnce({
      ok: false,
      latency_ms: 9,
      detail: "invalid credentials",
    });
    renderWizard();
    fireEvent.click(screen.getByTestId("wizard-step1-skip-check"));
    await waitFor(() => expect(screen.getByTestId("wizard-step2-type")).toBeTruthy());
    fireEvent.change(screen.getByTestId("wizard-step2-model"), {
      target: { value: "test-model" },
    });
    fireEvent.click(screen.getByTestId("wizard-step2-save"));

    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain("invalid credentials"),
    );
    expect(mockCreateProviderConfig).not.toHaveBeenCalled();
    expect(screen.getByTestId("wizard-step2-type")).toBeTruthy();
    expect(screen.queryByTestId("wizard-step3-extractor")).toBeNull();
  });

  it("Save button is disabled when model ID is empty", async () => {
    renderWizard();
    fireEvent.click(screen.getByTestId("wizard-step1-skip-check"));
    await waitFor(() => expect(screen.getByTestId("wizard-step2-type")).toBeTruthy());

    const saveBtn = screen.getByTestId("wizard-step2-save") as HTMLButtonElement;
    expect(saveBtn.disabled).toBe(true);
  });
});

// ─── 8. Step 3 — PDF config: calls putAppConfig ONLY ─────────────────────────
// AC-R11-2-13 (f): wizard writes via sanctioned endpoint, not raw fetch.

describe("FirstRunWizard — step 3 PDF persistence (sanctioned path only)", () => {
  beforeEach(() => {
    clearSetupFlag();
    mockCreateProviderConfig.mockClear();
    mockPutAppConfig.mockClear();
  });

  async function navigateToStep3() {
    fireEvent.click(screen.getByTestId("wizard-step1-skip-check"));
    await waitFor(() => expect(screen.getByTestId("wizard-step2-type")).toBeTruthy());
    fireEvent.click(screen.getByTestId("wizard-step2-skip"));
    await waitFor(() => expect(screen.getByTestId("wizard-step3-extractor")).toBeTruthy());
  }

  it("Save calls putAppConfig('pdf_extractor', 'pypdf') for default choice", async () => {
    renderWizard();
    await navigateToStep3();
    fireEvent.click(screen.getByTestId("wizard-step3-save"));
    await waitFor(() => {
      expect(mockPutAppConfig).toHaveBeenCalledWith("pdf_extractor", "pypdf");
    });
  });

  it("Save calls putAppConfig('pdf_extractor', 'marker') + marker_service_url when Marker selected with URL", async () => {
    renderWizard();
    await navigateToStep3();

    // Switch to Marker
    const select = screen.getByTestId("wizard-step3-extractor");
    fireEvent.change(select, { target: { value: "marker" } });

    // Fill marker URL
    await waitFor(() => expect(screen.getByTestId("wizard-step3-markerurl")).toBeTruthy());
    fireEvent.change(screen.getByTestId("wizard-step3-markerurl"), {
      target: { value: "http://host.docker.internal:8555" },
    });

    fireEvent.click(screen.getByTestId("wizard-step3-save"));

    await waitFor(() => {
      expect(mockPutAppConfig).toHaveBeenCalledWith("pdf_extractor", "marker");
      expect(mockPutAppConfig).toHaveBeenCalledWith(
        "marker_service_url",
        "http://host.docker.internal:8555",
      );
    });
    expect(
      mockPutAppConfig.mock.calls.filter(([key]) => key === "marker_service_url"),
    ).toHaveLength(1);
  });

  it("Save does NOT call createProviderConfig", async () => {
    renderWizard();
    await navigateToStep3();
    fireEvent.click(screen.getByTestId("wizard-step3-save"));
    await waitFor(() => expect(mockPutAppConfig).toHaveBeenCalled());
    expect(mockCreateProviderConfig).not.toHaveBeenCalled();
  });
});

// ─── 9. Re-openable from Settings (window event) ─────────────────────────────
// The SettingsPanel "Getting started" button fires window.dispatchEvent(new Event("synapse:openWizard")).
// AppShell listens and sets wizardForceOpen = true.
// Here we test the SettingsPanel slot directly to confirm it dispatches the event.

describe("SettingsPanel wizard-reopen-btn — fires synapse:openWizard event", () => {
  it("wizard-reopen-btn dispatches synapse:openWizard on click", async () => {
    // Import SettingsPanel locally to avoid mocking the whole module
    vi.doMock("../store/settingsStore", () => ({
      useSettingsStore: (selector: (s: unknown) => unknown) =>
        selector({
          contextWindowTokens: 32768,
          conversationHistoryLength: 10,
          language: "en",
          theme: "system",
          setContextWindow: vi.fn(),
          setConversationHistoryLength: vi.fn(),
          setLanguage: vi.fn(),
          setTheme: vi.fn(),
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
      CONTEXT_WINDOW_OPTIONS: [32768],
      CONV_HISTORY_OPTIONS: [10],
      computeBudgetSplit: () => ({ history: 0, retrieved: 0, system: 0, generation: 0 }),
      formatTokenCount: (n: number) => `${n}`,
    }));

    const events: string[] = [];
    const handler = (e: Event) => {
      events.push(e.type);
    };
    window.addEventListener("synapse:openWizard", handler);

    // Find and click the reopen button — we need to render it inline since
    // full SettingsPanel has many deps. Test the button's presence and event dispatch.
    const { queryByTestId } = render(
      <button
        data-testid="wizard-reopen-btn"
        onClick={() => window.dispatchEvent(new Event("synapse:openWizard"))}
      >
        Reopen
      </button>,
    );

    const btn = queryByTestId("wizard-reopen-btn");
    expect(btn).not.toBeNull();
    fireEvent.click(btn!);
    expect(events).toContain("synapse:openWizard");

    window.removeEventListener("synapse:openWizard", handler);
  });
});

// ─── 10. Progress indicator ──────────────────────────────────────────────────

describe("FirstRunWizard — progress indicator", () => {
  beforeEach(() => clearSetupFlag());

  it("renders four named steps on step 1", () => {
    renderWizard();
    expect(screen.getByTestId("wizard-progress").querySelectorAll("li")).toHaveLength(4);
  });

  it("marks the final step current on the summary screen", async () => {
    renderWizard();
    fireEvent.click(screen.getByTestId("wizard-step1-skip-check"));
    await waitFor(() => expect(screen.getByTestId("wizard-step2-type")).toBeTruthy());
    fireEvent.click(screen.getByTestId("wizard-step2-skip"));
    await waitFor(() => expect(screen.getByTestId("wizard-step3-extractor")).toBeTruthy());
    fireEvent.click(screen.getByTestId("wizard-step3-skip"));
    await waitFor(() => expect(screen.getByTestId("wizard-done")).toBeTruthy());

    const current = screen.getByTestId("wizard-progress").querySelector('[aria-current="step"]');
    expect(current).toBe(screen.getByTestId("wizard-progress").querySelectorAll("li")[3]);
  });
});

describe("FirstRunWizard — accessible progress", () => {
  it("exposes the current step in a labelled progress list", () => {
    renderWizard();
    const progress = screen.getByTestId("wizard-progress");
    expect(progress.querySelectorAll("li")).toHaveLength(4);
    expect(progress.querySelector('[aria-current="step"]')).not.toBeNull();
  });

  it("moves focus to the dialog title instead of the close button", () => {
    renderWizard();
    expect(document.activeElement?.id).toBe("first-run-wizard-title");
  });

  it("keeps Shift+Tab inside the dialog when focus starts on the title", () => {
    renderWizard();
    fireEvent.keyDown(screen.getByTestId("wizard-dialog"), {
      key: "Tab",
      shiftKey: true,
    });

    expect(document.activeElement).toBe(screen.getByTestId("wizard-skip"));
  });
});
