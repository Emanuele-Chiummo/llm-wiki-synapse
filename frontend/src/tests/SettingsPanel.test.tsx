/**
 * SettingsPanel.test.tsx — vitest unit tests for the M4-HARD settings panel.
 *
 * Covers:
 *   AC-HARD-SET-1/2: 9 sub-nav items render; clicking each switches the right pane.
 *   AC-HARD-SET-3/4: placeholder sections (Embeddings, API+MCP, Interface) render ComingSoonBadge.
 *   AC-HARD-PROV-1/2: provider list renders; ADD form toggles on button click.
 *   ITEM 2 (architect C2): Add button is disabled when model_id is empty.
 *   ITEM 4 (DEFECT-M4H-005): arrow-key navigation switches active section.
 *   AC-HARD-SET-5: keyboard navigation works.
 *   AC-HARD-SET-6: sub-nav buttons carry aria-current on active item.
 *
 * Not tested here (Playwright E2E):
 *   - Actual POST/DELETE network calls (mocked at store level here)
 *   - Panel resize assertions (AC-HARD-COL-*)
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SettingsPanel } from "../components/settings/SettingsPanel";

// ─── Mock i18n ────────────────────────────────────────────────────────────────

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, _opts?: object) => {
      // Return the leaf key for predictable assertions
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
      setContextWindow: vi.fn(),
      setConversationHistoryLength: vi.fn(),
      setLanguage: vi.fn(),
      reset: vi.fn(),
    }),
  selectContextWindow: (s: { contextWindowTokens: number }) => s.contextWindowTokens,
  selectConversationHistoryLength: (s: { conversationHistoryLength: number }) =>
    s.conversationHistoryLength,
  selectLanguage: (s: { language: string }) => s.language,
  selectSetContextWindow: (s: { setContextWindow: unknown }) => s.setContextWindow,
  selectSetConversationHistoryLength: (s: { setConversationHistoryLength: unknown }) =>
    s.setConversationHistoryLength,
  selectSetLanguage: (s: { setLanguage: unknown }) => s.setLanguage,
  selectResetSettings: (s: { reset: unknown }) => s.reset,
  CONTEXT_WINDOW_OPTIONS: [4096, 8192, 16384, 32768, 65536, 131072, 262144, 524288, 1048576],
  CONV_HISTORY_OPTIONS: [2, 4, 6, 8, 10, 20],
  computeBudgetSplit: (tokens: number) => ({
    history: Math.round(tokens * 0.6),
    retrieved: Math.round(tokens * 0.2),
    system: Math.round(tokens * 0.05),
    generation: Math.round(tokens * 0.15),
  }),
  formatTokenCount: (n: number) => {
    if (n >= 1048576) return `${n / 1048576}M`;
    if (n >= 1024) return `${n / 1024}K`;
    return `${n}`;
  },
}));

// ─── Mock providerStore ───────────────────────────────────────────────────────

const mockProviderList = [
  {
    id: "prov-1",
    scope: "global",
    operation: null,
    vault_id: null,
    provider_type: "api",
    model_id: "claude-sonnet-4-6",
    base_url: null,
    max_iter: 3,
    token_budget: 60000,
    is_fallback: false,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
  {
    id: "prov-2",
    scope: "global",
    operation: null,
    vault_id: null,
    provider_type: "local",
    model_id: "llama3",
    base_url: null,
    max_iter: 3,
    token_budget: 60000,
    is_fallback: true,
    created_at: "2026-01-02T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
  },
];

const mockFetchProviders = vi.fn();
const mockAddProvider = vi.fn();
const mockDeleteProvider = vi.fn();

vi.mock("../store/providerStore", () => ({
  useProviderStore: (selector: (s: unknown) => unknown) =>
    selector({
      list: mockProviderList,
      loading: false,
      error: null,
      fetchList: mockFetchProviders,
      addProvider: mockAddProvider,
      deleteProvider: mockDeleteProvider,
    }),
  useShallow: (fn: unknown) => fn,
  selectProviderList: (s: { list: unknown[] }) => s.list,
  selectProviderLoading: (s: { loading: boolean }) => s.loading,
  selectProviderError: (s: { error: string | null }) => s.error,
  selectFetchProviderList: (s: { fetchList: unknown }) => s.fetchList,
  selectAddProvider: (s: { addProvider: unknown }) => s.addProvider,
  selectDeleteProvider: (s: { deleteProvider: unknown }) => s.deleteProvider,
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
  ImportScheduleCard: () => <div data-testid="import-schedule-card">ImportScheduleCard</div>,
}));

// ─── Mock providerClient (fetchEmbeddingConfig) ───────────────────────────────

vi.mock("../api/providerClient", async (importOriginal) => {
  const orig = await importOriginal<typeof import("../api/providerClient")>();
  return {
    ...orig,
    fetchEmbeddingConfig: vi.fn().mockResolvedValue({
      embedding_url: "http://localhost:11434/api/embeddings",
      embedding_model: "bge-m3",
      embedding_dim: 1024,
    }),
  };
});

// ─── Helpers ──────────────────────────────────────────────────────────────────

function renderPanel() {
  return render(<SettingsPanel />);
}

// ─── 1. All 9 sub-nav items render ───────────────────────────────────────────

describe("SettingsPanel — 9 sub-nav items (AC-HARD-SET-1/3)", () => {
  beforeEach(() => {
    renderPanel();
  });

  // The i18n mock returns the last segment of the key, so e.g.
  // "settings.nav.general" → "general"
  const EXPECTED_SECTION_IDS = [
    "general",
    "llmModels",
    "embeddings",
    "sourceWatch",
    "apiMcp",
    "output",
    "interface",
    "maintenance",
    "about",
  ] as const;

  it("renders exactly 9 section buttons in the left nav aside", () => {
    const aside = document.querySelector("aside");
    expect(aside).not.toBeNull();
    const buttons = aside!.querySelectorAll("button");
    expect(buttons).toHaveLength(9);
  });

  EXPECTED_SECTION_IDS.forEach((sectionId) => {
    it(`renders a button for section "${sectionId}"`, () => {
      const btn = document.querySelector(`[data-settings-section="${sectionId}"]`);
      expect(btn, `Button for section "${sectionId}" should be in the DOM`).not.toBeNull();
    });
  });
});

// ─── 2. Clicking each sub-nav item switches the right pane ───────────────────

describe("SettingsPanel — section switching (AC-HARD-SET-2)", () => {
  it("clicking LLM Models nav item shows LLM Models content", () => {
    renderPanel();
    const llmBtn = document.querySelector('[data-settings-section="llmModels"]');
    expect(llmBtn).not.toBeNull();
    fireEvent.click(llmBtn!);
    // Provider list should be visible (SectionLlmModels rendered)
    // The section header uses the i18n key "settings.nav.llmModels" → "llmModels"
    // The desc is "settings.llmModels.desc" → "desc"
    // Check provider rows are present
    const deleteButtons = screen.getAllByText("delete");
    expect(deleteButtons.length).toBeGreaterThanOrEqual(2);
  });

  it("clicking Output nav item shows Output content", () => {
    renderPanel();
    const outputBtn = document.querySelector('[data-settings-section="output"]');
    expect(outputBtn).not.toBeNull();
    fireEvent.click(outputBtn!);
    // Output section has convHistory buttons (2, 4, 6, 8, 10, 20)
    expect(screen.getByText("10")).toBeTruthy(); // default selected value
  });

  it("clicking Embeddings shows the embedding config section (not a ComingSoonBadge)", () => {
    renderPanel();
    const embBtn = document.querySelector('[data-settings-section="embeddings"]');
    expect(embBtn).not.toBeNull();
    fireEvent.click(embBtn!);
    // SectionEmbeddings now shows a real config display (loading or data), not a stub
    // The "loading" i18n key is shown while the fetch resolves
    expect(screen.getByText("loading")).toBeTruthy();
  });

  it("clicking API+MCP shows the ComingSoonBadge placeholder", () => {
    renderPanel();
    const apiBtn = document.querySelector('[data-settings-section="apiMcp"]');
    expect(apiBtn).not.toBeNull();
    fireEvent.click(apiBtn!);
    // comingSoon key appears in the badge
    const badges = screen.getAllByText("comingSoon");
    expect(badges.length).toBeGreaterThanOrEqual(1);
  });

  it("clicking Interface shows the ComingSoonBadge placeholder", () => {
    renderPanel();
    const ifBtn = document.querySelector('[data-settings-section="interface"]');
    expect(ifBtn).not.toBeNull();
    fireEvent.click(ifBtn!);
    const badges = screen.getAllByText("comingSoon");
    expect(badges.length).toBeGreaterThanOrEqual(1);
  });

  it("active button has aria-current='true'", () => {
    renderPanel();
    // Default active section is "general"
    const generalBtn = document.querySelector('[data-settings-section="general"]');
    expect(generalBtn?.getAttribute("aria-current")).toBe("true");
  });

  it("non-active buttons do NOT have aria-current", () => {
    renderPanel();
    const llmBtn = document.querySelector('[data-settings-section="llmModels"]');
    expect(llmBtn?.getAttribute("aria-current")).toBeNull();
  });

  it("after clicking LLM Models, llmModels button has aria-current='true'", () => {
    renderPanel();
    const llmBtn = document.querySelector('[data-settings-section="llmModels"]');
    fireEvent.click(llmBtn!);
    expect(llmBtn?.getAttribute("aria-current")).toBe("true");
  });
});

// ─── 3. Placeholder sections render ComingSoonBadge (AC-HARD-SET-4) ──────────
// Note: "embeddings" was removed from PLACEHOLDER_SECTIONS — it now renders a real
// config display (GET /config/embedding) rather than a stub badge.

describe("SettingsPanel — ComingSoonBadge on placeholder sections (AC-HARD-SET-4)", () => {
  const PLACEHOLDER_SECTIONS = ["apiMcp", "interface"] as const;

  PLACEHOLDER_SECTIONS.forEach((sectionId) => {
    it(`section "${sectionId}" renders a comingSoon message (not empty)`, () => {
      renderPanel();
      const btn = document.querySelector(`[data-settings-section="${sectionId}"]`);
      fireEvent.click(btn!);
      // comingSoon key should be rendered — could appear multiple times
      const elements = screen.getAllByText("comingSoon");
      expect(elements.length).toBeGreaterThan(0);
    });
  });
});

// ─── 4. Provider list renders (AC-HARD-PROV-1) ───────────────────────────────

describe("SettingsPanel — LLM Models section renders provider list (AC-HARD-PROV-1)", () => {
  beforeEach(() => {
    renderPanel();
    const llmBtn = document.querySelector('[data-settings-section="llmModels"]');
    fireEvent.click(llmBtn!);
  });

  it("renders 2 provider rows (matching mock data)", () => {
    const deleteButtons = screen.getAllByText("delete");
    expect(deleteButtons).toHaveLength(2);
  });

  it("renders the model_id for each provider", () => {
    expect(screen.getByText("claude-sonnet-4-6")).toBeTruthy();
    expect(screen.getByText("llama3")).toBeTruthy();
  });
});

// ─── 5. ADD form toggles (AC-HARD-PROV-2) ────────────────────────────────────

describe("SettingsPanel — ADD form visibility toggle (AC-HARD-PROV-2)", () => {
  beforeEach(() => {
    renderPanel();
    const llmBtn = document.querySelector('[data-settings-section="llmModels"]');
    fireEvent.click(llmBtn!);
  });

  it("ADD form is not visible before clicking addProvider", () => {
    // The form's model_id input is not in the DOM yet
    expect(document.querySelector('input[type="text"]')).toBeNull();
  });

  it("clicking '+ addProvider' button shows the form", () => {
    // The top-level button text includes "addProvider" (from i18n key last segment)
    const addBtn = screen.getByText(/addProvider/i);
    fireEvent.click(addBtn);
    // Now the model_id text input should be in the DOM
    expect(document.querySelector('input[type="text"]')).not.toBeNull();
  });
});

// ─── 6. Add button disabled when model_id empty (ITEM 2 / architect C2) ──────

describe("SettingsPanel — Add button disabled when model_id empty (architect C2)", () => {
  beforeEach(() => {
    renderPanel();
    const llmBtn = document.querySelector('[data-settings-section="llmModels"]');
    fireEvent.click(llmBtn!);
    // Open the add form
    const addBtn = screen.getByText(/addProvider/i);
    fireEvent.click(addBtn);
  });

  it("Add button is disabled when model_id field is empty", () => {
    // Find the submit button inside the form ("add" key → text "add")
    const submitBtn = screen.getByText("add") as HTMLButtonElement;
    expect(submitBtn.disabled).toBe(true);
  });

  it("Add button is enabled after typing a model_id", () => {
    const input = document.querySelector('input[type="text"]') as HTMLInputElement;
    fireEvent.change(input, { target: { value: "claude-sonnet-4-6" } });
    const submitBtn = screen.getByText("add") as HTMLButtonElement;
    expect(submitBtn.disabled).toBe(false);
  });

  it("Add button is disabled again after clearing model_id", () => {
    const input = document.querySelector('input[type="text"]') as HTMLInputElement;
    fireEvent.change(input, { target: { value: "claude-sonnet-4-6" } });
    fireEvent.change(input, { target: { value: "" } });
    const submitBtn = screen.getByText("add") as HTMLButtonElement;
    expect(submitBtn.disabled).toBe(true);
  });

  it("Add button is disabled when model_id is only whitespace", () => {
    const input = document.querySelector('input[type="text"]') as HTMLInputElement;
    fireEvent.change(input, { target: { value: "   " } });
    const submitBtn = screen.getByText("add") as HTMLButtonElement;
    expect(submitBtn.disabled).toBe(true);
  });
});

// ─── 7. Arrow-key nav switches sections (ITEM 4 / DEFECT-M4H-005) ────────────

describe("SettingsPanel — arrow-key navigation in left sub-nav (DEFECT-M4H-005)", () => {
  it("ArrowDown from 'general' (index 0) moves to 'llmModels' (index 1)", () => {
    renderPanel();
    const aside = document.querySelector("aside")!;
    // Initial active = general
    expect(document.querySelector('[data-settings-section="general"]')?.getAttribute("aria-current")).toBe("true");

    fireEvent.keyDown(aside, { key: "ArrowDown" });
    // After ArrowDown, llmModels should be active
    expect(document.querySelector('[data-settings-section="llmModels"]')?.getAttribute("aria-current")).toBe("true");
    expect(document.querySelector('[data-settings-section="general"]')?.getAttribute("aria-current")).toBeNull();
  });

  it("ArrowDown cycles past 'about' (last) back to 'general' (first)", () => {
    renderPanel();
    const aside = document.querySelector("aside")!;
    // Navigate to "about" (index 8) — 8 ArrowDown presses from "general"
    for (let i = 0; i < 8; i++) {
      fireEvent.keyDown(aside, { key: "ArrowDown" });
    }
    expect(document.querySelector('[data-settings-section="about"]')?.getAttribute("aria-current")).toBe("true");

    // One more ArrowDown should wrap to "general"
    fireEvent.keyDown(aside, { key: "ArrowDown" });
    expect(document.querySelector('[data-settings-section="general"]')?.getAttribute("aria-current")).toBe("true");
  });

  it("ArrowUp from 'general' (index 0) wraps to 'about' (index 8)", () => {
    renderPanel();
    const aside = document.querySelector("aside")!;
    fireEvent.keyDown(aside, { key: "ArrowUp" });
    expect(document.querySelector('[data-settings-section="about"]')?.getAttribute("aria-current")).toBe("true");
  });

  it("Home key moves focus to 'general' (index 0)", () => {
    renderPanel();
    const aside = document.querySelector("aside")!;
    // Move to llmModels first
    fireEvent.keyDown(aside, { key: "ArrowDown" });
    expect(document.querySelector('[data-settings-section="llmModels"]')?.getAttribute("aria-current")).toBe("true");

    fireEvent.keyDown(aside, { key: "Home" });
    expect(document.querySelector('[data-settings-section="general"]')?.getAttribute("aria-current")).toBe("true");
  });

  it("End key moves focus to 'about' (index 8)", () => {
    renderPanel();
    const aside = document.querySelector("aside")!;
    fireEvent.keyDown(aside, { key: "End" });
    expect(document.querySelector('[data-settings-section="about"]')?.getAttribute("aria-current")).toBe("true");
  });

  it("non-arrow keys do not change the active section", () => {
    renderPanel();
    const aside = document.querySelector("aside")!;
    fireEvent.keyDown(aside, { key: "Tab" });
    expect(document.querySelector('[data-settings-section="general"]')?.getAttribute("aria-current")).toBe("true");
  });
});

// ─── 8. Source Watch renders ImportScheduleCard ───────────────────────────────

describe("SettingsPanel — Source Watch section (AC-HARD-SET-4)", () => {
  it("shows the ImportScheduleCard in the Source Watch section", () => {
    renderPanel();
    const swBtn = document.querySelector('[data-settings-section="sourceWatch"]');
    fireEvent.click(swBtn!);
    expect(screen.getByTestId("import-schedule-card")).toBeTruthy();
  });
});

// ─── 9. Maintenance section renders reset button ─────────────────────────────

describe("SettingsPanel — Maintenance section", () => {
  it("renders the reset button with testid settings-reset-btn", () => {
    renderPanel();
    const maintBtn = document.querySelector('[data-settings-section="maintenance"]');
    fireEvent.click(maintBtn!);
    expect(screen.getByTestId("settings-reset-btn")).toBeTruthy();
  });
});

// ─── 10. About section renders version info ───────────────────────────────────

describe("SettingsPanel — About section", () => {
  it("renders the version string 'v0.4'", () => {
    renderPanel();
    const aboutBtn = document.querySelector('[data-settings-section="about"]');
    fireEvent.click(aboutBtn!);
    expect(screen.getByText("v0.4")).toBeTruthy();
  });
});
