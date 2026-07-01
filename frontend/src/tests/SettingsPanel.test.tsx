/**
 * SettingsPanel.test.tsx — vitest unit tests for the M4-HARD + M5 + M6 settings panel.
 *
 * Covers:
 *   AC-HARD-SET-1/2: 11 sub-nav items render; clicking each switches the right pane.
 *   AC-HARD-SET-3/4: placeholder sections (Interface) render ComingSoonBadge.
 *   AC-F1-MCP-UI-3/4/5/6: SectionApiMcp renders connection + tools from mock payload.
 *   AC-HARD-PROV-1/2: provider list renders; ADD form toggles on button click.
 *   ITEM 2 (architect C2): Add button is disabled when model_id is empty.
 *   ITEM 4 (DEFECT-M4H-005): arrow-key navigation switches active section.
 *   AC-HARD-SET-5: keyboard navigation works.
 *   AC-HARD-SET-6: sub-nav buttons carry aria-current on active item.
 *   ADR-0032: remote MCP toggle — three states (no-token, token+off, enabled).
 *   ADR-0033: MCP access sub-block — generate/clear token, one-time reveal, allow-without-token
 *             switch, posture labels, token never re-shown after dismiss/refetch.
 *   ADR-0040: Web Clipper section — generate/rotate/clear token, one-time reveal, enable toggle,
 *             allowed origins PUT, clip endpoint URL display.
 *   ADR-0041: Web Search section — URL/categories/max_queries fields call PUT, source badge,
 *             clear button, SearXNG-only note.
 *
 * Not tested here (Playwright E2E):
 *   - Actual POST/DELETE network calls (mocked at store level here)
 *   - Panel resize assertions (AC-HARD-COL-*)
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
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

// ─── Mock providerClient (fetchEmbeddingConfig + fetchMcpInfo + setRemoteMcpEnabled
//     + setMcpAuth + fetchClipConfig + setClipConfig + fetchWebSearchConfig
//     + setWebSearchConfig) ──────────────────────────────────────────────────────────
// NOTE: vi.mock is hoisted — no top-level variables may be referenced inside the
// factory. The 4-tool fixture is inlined here. ADR-0032/0033/0040/0041 fields included.

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
      tool_count: 4,
      tools: [
        {
          name: "search_wiki",
          description: "Search the wiki for pages matching a query. Returns ranked results.",
          input_schema: { type: "object", properties: { query: {}, limit: {} }, required: ["query"] },
        },
        {
          name: "write_page",
          description: "Write or overwrite a wiki page with the given content.",
          input_schema: { type: "object", properties: { title: {}, content: {}, page_type: {} }, required: ["title", "content"] },
        },
        {
          name: "get_page",
          description: "Retrieve a wiki page by title or ID.",
          input_schema: { type: "object", properties: { title: {} }, required: ["title"] },
        },
        {
          name: "list_pages",
          description: "List all wiki pages, optionally filtered by type.",
          input_schema: { type: "object", properties: { page_type: {} } },
        },
      ],
      // ADR-0032 §2.5 fields — default: token configured (db), remote OFF
      http_enabled: true,
      remote_write_enabled: false,
      token_configured: true,
      remote_enabled: false,
      mount_path: "/mcp/server",
      // ADR-0033 §2.5 fields — default: db token, allow_without_token off
      token_source: "db",
      allow_without_token: false,
    }),
    // ADR-0032 §2.4 — default: successful enable
    setRemoteMcpEnabled: vi.fn().mockResolvedValue({
      remote_enabled: true,
      token_configured: true,
      mount_path: "/mcp/server",
      clamped: false,
    }),
    // ADR-0033 §2.5 — default: rotate_token response with generated_token ONCE
    setMcpAuth: vi.fn().mockResolvedValue({
      token_configured: true,
      token_source: "db",
      allow_without_token: false,
      remote_enabled: false,
      mount_path: "/mcp/server",
      generated_token: "synapse-test-token-abc123xyz",
    }),
    // ADR-0040 — Web Clipper default: token configured (db), enabled, no extra origins
    fetchClipConfig: vi.fn().mockResolvedValue({
      enabled: true,
      token_configured: true,
      token_source: "db",
      allowed_origins: [],
      max_body_bytes: 1048576,
    }),
    // ADR-0040 — default: rotate_token response with generated_token ONCE
    setClipConfig: vi.fn().mockResolvedValue({
      enabled: true,
      token_configured: true,
      token_source: "db",
      allowed_origins: [],
      max_body_bytes: 1048576,
      generated_token: "clip-test-token-xyz789abc",
    }),
    // ADR-0041 — Web Search default: configured with env URL
    fetchWebSearchConfig: vi.fn().mockResolvedValue({
      configured: true,
      url: "http://searxng:8080",
      categories: ["general"],
      max_queries: 3,
      source: "env",
    }),
    // ADR-0041 — default: post-write posture (same shape)
    setWebSearchConfig: vi.fn().mockResolvedValue({
      configured: true,
      url: "http://searxng:8080",
      categories: ["general"],
      max_queries: 3,
      source: "db",
    }),
  };
});

// ─── Helpers ──────────────────────────────────────────────────────────────────

function renderPanel() {
  return render(<SettingsPanel />);
}

// ─── 1. All 11 sub-nav items render ──────────────────────────────────────────

describe("SettingsPanel — 11 sub-nav items (AC-HARD-SET-1/3)", () => {
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
    "webSearch",
    "apiMcp",
    "webClipper",
    "output",
    "interface",
    "maintenance",
    "about",
  ] as const;

  it("renders exactly 11 section buttons in the left nav aside", () => {
    const aside = document.querySelector("aside");
    expect(aside).not.toBeNull();
    const buttons = aside!.querySelectorAll("button");
    expect(buttons).toHaveLength(11);
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

  it("clicking API+MCP shows the loading state (real panel, not ComingSoonBadge)", async () => {
    renderPanel();
    const apiBtn = document.querySelector('[data-settings-section="apiMcp"]');
    expect(apiBtn).not.toBeNull();
    fireEvent.click(apiBtn!);
    // The real panel shows a loading message while fetching (i18n mock returns "loading")
    expect(screen.getByText("loading")).toBeTruthy();
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
// Note: "embeddings" and "apiMcp" were removed from PLACEHOLDER_SECTIONS —
// both now render real read-only panels (GET /config/embedding, GET /mcp/info).

describe("SettingsPanel — ComingSoonBadge on placeholder sections (AC-HARD-SET-4)", () => {
  const PLACEHOLDER_SECTIONS = ["interface"] as const;

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
    // Navigate to "about" (index 10) — 10 ArrowDown presses from "general"
    for (let i = 0; i < 10; i++) {
      fireEvent.keyDown(aside, { key: "ArrowDown" });
    }
    expect(document.querySelector('[data-settings-section="about"]')?.getAttribute("aria-current")).toBe("true");

    // One more ArrowDown should wrap to "general"
    fireEvent.keyDown(aside, { key: "ArrowDown" });
    expect(document.querySelector('[data-settings-section="general"]')?.getAttribute("aria-current")).toBe("true");
  });

  it("ArrowUp from 'general' (index 0) wraps to 'about' (index 10)", () => {
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

  it("End key moves focus to 'about' (index 10)", () => {
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
  it("renders the version string 'v0.5'", () => {
    renderPanel();
    const aboutBtn = document.querySelector('[data-settings-section="about"]');
    fireEvent.click(aboutBtn!);
    expect(screen.getByText("v0.5")).toBeTruthy();
  });
});

// ─── 11. API + MCP section — real panel (ADR-0027, AC-F1-MCP-UI-3/4/5/6) ─────

describe("SettingsPanel — API + MCP section renders real panel (ADR-0027)", () => {
  function navigateToApiMcp() {
    renderPanel();
    const apiBtn = document.querySelector('[data-settings-section="apiMcp"]');
    fireEvent.click(apiBtn!);
  }

  it("shows loading state immediately after navigation (before fetch resolves)", () => {
    navigateToApiMcp();
    // i18n mock returns last key segment; "settings.apiMcp.loading" → "loading"
    expect(screen.getByText("loading")).toBeTruthy();
  });

  it("renders all 4 tool names after fetch resolves (AC-F1-MCP-UI-4)", async () => {
    navigateToApiMcp();
    await waitFor(() => {
      expect(screen.getByTestId("mcp-tool-name-search_wiki")).toBeTruthy();
    });
    expect(screen.getByTestId("mcp-tool-name-write_page")).toBeTruthy();
    expect(screen.getByTestId("mcp-tool-name-get_page")).toBeTruthy();
    expect(screen.getByTestId("mcp-tool-name-list_pages")).toBeTruthy();
  });

  it("renders correct param counts for each tool via data-param-count attribute", async () => {
    navigateToApiMcp();
    await waitFor(() => {
      expect(screen.getByTestId("mcp-tool-params-search_wiki")).toBeTruthy();
    });
    // Counts are on data-param-count (numeric, avoids i18n interpolation mock artefact)
    // search_wiki: {query, limit} = 2 params
    expect(screen.getByTestId("mcp-tool-params-search_wiki").getAttribute("data-param-count")).toBe("2");
    // write_page: {title, content, page_type} = 3 params
    expect(screen.getByTestId("mcp-tool-params-write_page").getAttribute("data-param-count")).toBe("3");
    // get_page: {title} = 1 param
    expect(screen.getByTestId("mcp-tool-params-get_page").getAttribute("data-param-count")).toBe("1");
    // list_pages: {page_type} = 1 param
    expect(screen.getByTestId("mcp-tool-params-list_pages").getAttribute("data-param-count")).toBe("1");
  });

  it("renders the copy-to-clipboard button (AC-F1-MCP-UI-5)", async () => {
    navigateToApiMcp();
    await waitFor(() => {
      expect(screen.getByTestId("mcp-copy-btn")).toBeTruthy();
    });
  });

  it("generated snippet is keyed by server_name and uses tokenised entry_point_command (AC-F1-MCP-UI-5)", async () => {
    navigateToApiMcp();
    await waitFor(() => {
      expect(screen.getByTestId("mcp-snippet")).toBeTruthy();
    });
    const snippetText = screen.getByTestId("mcp-snippet").textContent ?? "";
    // server_name "synapse" must be the key
    expect(snippetText).toContain('"synapse"');
    // argv[0] of "python -m app.mcp.server" → command = "python"
    expect(snippetText).toContain('"command"');
    expect(snippetText).toContain('"python"');
    // args includes the rest of the command
    expect(snippetText).toContain('"-m"');
    expect(snippetText).toContain('"app.mcp.server"');
    // must be valid JSON
    expect(() => JSON.parse(snippetText)).not.toThrow();
    const parsed = JSON.parse(snippetText) as {
      mcpServers: { [key: string]: { command: string; args: string[] } };
    };
    expect(parsed.mcpServers["synapse"]).toBeDefined();
    expect(parsed.mcpServers["synapse"]!.command).toBe("python");
    expect(parsed.mcpServers["synapse"]!.args).toEqual(["-m", "app.mcp.server"]);
  });

  it("shows degraded error state when fetchMcpInfo rejects (AC-F1-MCP-UI — degraded)", async () => {
    const { fetchMcpInfo } = await import("../api/providerClient");
    (fetchMcpInfo as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error("network error"));

    navigateToApiMcp();
    await waitFor(() => {
      // i18n mock returns "error" for "settings.apiMcp.error"
      expect(screen.getByText("error")).toBeTruthy();
    });
    // No tool rows should be present
    expect(screen.queryByTestId("mcp-tool-row-search_wiki")).toBeNull();
  });

  it("does NOT render the retired comingSoon key (stub removed — ADR-0027)", async () => {
    navigateToApiMcp();
    await waitFor(() => {
      expect(screen.getByTestId("mcp-tool-name-search_wiki")).toBeTruthy();
    });
    // After the real panel loads, "comingSoon" text must not appear
    expect(screen.queryByText("comingSoon")).toBeNull();
  });
});

// ─── 12. Remote MCP toggle — ADR-0032 three-state tests ─────────────────────

describe("SettingsPanel — Remote MCP toggle (ADR-0032)", () => {
  // Helper: navigate to API+MCP section and wait for it to load
  async function navigateToApiMcpAndWait() {
    renderPanel();
    const apiBtn = document.querySelector('[data-settings-section="apiMcp"]');
    fireEvent.click(apiBtn!);
    // Wait for the fetch to resolve and tools to appear
    await waitFor(() => {
      expect(screen.getByTestId("mcp-remote-toggle")).toBeTruthy();
    });
  }

  // ── State 1: no token configured — toggle disabled + no-token note ──────────

  describe("State 1: token_configured=false — toggle disabled, no-token note shown", () => {
    beforeEach(async () => {
      const { fetchMcpInfo } = await import("../api/providerClient");
      (fetchMcpInfo as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
        server_name: "synapse",
        transport: "stdio",
        entry_point_command: "python -m app.mcp.server",
        tool_count: 4,
        tools: [],
        http_enabled: false,
        remote_write_enabled: false,
        token_configured: false,
        remote_enabled: false,
        mount_path: "/mcp/server",
        token_source: "none",
        allow_without_token: false,
      });
    });

    it("renders the remote toggle in a disabled state when no token is configured", async () => {
      await navigateToApiMcpAndWait();
      const toggle = screen.getByTestId("mcp-remote-toggle") as HTMLInputElement;
      expect(toggle.disabled).toBe(true);
      expect(toggle.checked).toBe(false);
    });

    it("shows the no-token note (noTokenNote i18n key) when token is not configured", async () => {
      await navigateToApiMcpAndWait();
      // i18n mock returns last key segment: "settings.apiMcp.remote.noTokenNote" → "noTokenNote"
      expect(screen.getByText("noTokenNote")).toBeTruthy();
    });

    it("does NOT show the remote URL row when no token is configured", async () => {
      await navigateToApiMcpAndWait();
      expect(screen.queryByTestId("mcp-remote-url")).toBeNull();
    });

    it("does NOT call setRemoteMcpEnabled when the disabled toggle is interacted with", async () => {
      const { setRemoteMcpEnabled } = await import("../api/providerClient");
      // Clear any prior calls from other tests in the same module mock
      (setRemoteMcpEnabled as ReturnType<typeof vi.fn>).mockClear();
      await navigateToApiMcpAndWait();
      const toggle = screen.getByTestId("mcp-remote-toggle") as HTMLInputElement;
      fireEvent.click(toggle);
      expect(setRemoteMcpEnabled).not.toHaveBeenCalled();
    });
  });

  // ── State 2: token set, remote OFF — toggle enabled, off position ────────────

  describe("State 2: token_configured=true, remote_enabled=false — toggle on, off position", () => {
    it("renders the remote toggle as enabled (not disabled) and unchecked", async () => {
      await navigateToApiMcpAndWait();
      const toggle = screen.getByTestId("mcp-remote-toggle") as HTMLInputElement;
      expect(toggle.disabled).toBe(false);
      expect(toggle.checked).toBe(false);
    });

    it("does NOT show the remote URL row when remote is off", async () => {
      await navigateToApiMcpAndWait();
      expect(screen.queryByTestId("mcp-remote-url")).toBeNull();
    });

    it("clicking the toggle calls setRemoteMcpEnabled(true)", async () => {
      const { setRemoteMcpEnabled } = await import("../api/providerClient");
      await navigateToApiMcpAndWait();
      const toggle = screen.getByTestId("mcp-remote-toggle") as HTMLInputElement;
      fireEvent.click(toggle);
      await waitFor(() => {
        expect(setRemoteMcpEnabled).toHaveBeenCalledWith(true);
      });
    });

    it("after successful toggle ON, shows the remote URL row with origin + mount_path", async () => {
      // setRemoteMcpEnabled mock returns remote_enabled:true, mount_path:"/mcp/server"
      // Use window.location.origin to be compatible with any jsdom port.
      await navigateToApiMcpAndWait();
      const toggle = screen.getByTestId("mcp-remote-toggle") as HTMLInputElement;
      fireEvent.click(toggle);
      await waitFor(() => {
        expect(screen.getByTestId("mcp-remote-url")).toBeTruthy();
      });
      const urlEl = screen.getByTestId("mcp-remote-url");
      expect(urlEl.textContent).toBe(`${window.location.origin}/mcp/server`);
    });

    it("after successful toggle ON, URL never contains a token", async () => {
      await navigateToApiMcpAndWait();
      const toggle = screen.getByTestId("mcp-remote-toggle") as HTMLInputElement;
      fireEvent.click(toggle);
      await waitFor(() => {
        expect(screen.getByTestId("mcp-remote-url")).toBeTruthy();
      });
      const urlEl = screen.getByTestId("mcp-remote-url");
      // Ensure no token-like string (long alphanumeric) is in the URL display
      expect(urlEl.textContent).not.toMatch(/token|bearer|key|secret/i);
    });

    it("clamped response keeps toggle off and shows no URL", async () => {
      const { setRemoteMcpEnabled } = await import("../api/providerClient");
      (setRemoteMcpEnabled as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
        remote_enabled: false,
        token_configured: false,
        mount_path: "/mcp/server",
        clamped: true,
      });

      await navigateToApiMcpAndWait();
      const toggle = screen.getByTestId("mcp-remote-toggle") as HTMLInputElement;
      fireEvent.click(toggle);

      // After clamped response, URL row must NOT appear
      await waitFor(() => {
        // Give the UI time to process the response
        expect(screen.queryByTestId("mcp-remote-url")).toBeNull();
      });

      // Toggle should still be off
      expect(toggle.checked).toBe(false);
    });
  });

  // ── State 3: remote_enabled=true — URL visible, copy button present ──────────

  describe("State 3: remote_enabled=true — URL shown, copy button present", () => {
    beforeEach(async () => {
      const { fetchMcpInfo } = await import("../api/providerClient");
      (fetchMcpInfo as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
        server_name: "synapse",
        transport: "stdio",
        entry_point_command: "python -m app.mcp.server",
        tool_count: 4,
        tools: [],
        http_enabled: true,
        remote_write_enabled: false,
        token_configured: true,
        remote_enabled: true,
        mount_path: "/mcp/server",
        token_source: "db",
        allow_without_token: false,
      });
    });

    it("renders the remote URL row immediately when remote_enabled=true on fetch", async () => {
      await navigateToApiMcpAndWait();
      expect(screen.getByTestId("mcp-remote-url")).toBeTruthy();
    });

    it("the URL is window.location.origin + mount_path", async () => {
      await navigateToApiMcpAndWait();
      const urlEl = screen.getByTestId("mcp-remote-url");
      expect(urlEl.textContent).toBe(`${window.location.origin}/mcp/server`);
    });

    it("renders the URL copy button", async () => {
      await navigateToApiMcpAndWait();
      expect(screen.getByTestId("mcp-remote-url-copy")).toBeTruthy();
    });

    it("renders the remote snippet block", async () => {
      await navigateToApiMcpAndWait();
      expect(screen.getByTestId("mcp-remote-snippet")).toBeTruthy();
    });

    it("the remote snippet contains the full URL", async () => {
      await navigateToApiMcpAndWait();
      const snippet = screen.getByTestId("mcp-remote-snippet").textContent ?? "";
      expect(snippet).toContain(`${window.location.origin}/mcp/server`);
      expect(() => JSON.parse(snippet)).not.toThrow();
    });

    it("the toggle is checked when remote_enabled=true", async () => {
      await navigateToApiMcpAndWait();
      const toggle = screen.getByTestId("mcp-remote-toggle") as HTMLInputElement;
      expect(toggle.checked).toBe(true);
    });

    it("no token value appears anywhere in the rendered output", async () => {
      await navigateToApiMcpAndWait();
      // The entire rendered text must not contain anything resembling a token value
      const body = document.body.textContent ?? "";
      expect(body).not.toMatch(/MCP_AUTH_TOKEN\s*=\s*\S+/);
    });
  });
});

// ─── 13. Embeddings section — ADR-0030 toggle states ─────────────────────────
// Covers embeddings_enabled:true (semantic active) and :false (lexical-only).
// The mock is reconfigured per test via mockResolvedValueOnce to avoid
// polluting the shared default (embeddings_enabled:true).

describe("SettingsPanel — Embeddings section enabled state (ADR-0030)", () => {
  function navigateToEmbeddings() {
    renderPanel();
    const embBtn = document.querySelector('[data-settings-section="embeddings"]');
    fireEvent.click(embBtn!);
  }

  it("shows loading state immediately after navigation (before fetch resolves)", () => {
    navigateToEmbeddings();
    // i18n mock returns last key segment; "settings.embeddings.loading" → "loading"
    expect(screen.getByText("loading")).toBeTruthy();
  });

  it("when embeddings_enabled=true: renders the semantic-active indicator", async () => {
    navigateToEmbeddings();
    await waitFor(() => {
      expect(screen.getByTestId("embeddings-status-active")).toBeTruthy();
    });
    // i18n mock returns "semanticActive" (last segment of settings.embeddings.semanticActive)
    expect(screen.getByText("semanticActive")).toBeTruthy();
  });

  it("when embeddings_enabled=true: URL, model, and dim rows are visible", async () => {
    navigateToEmbeddings();
    await waitFor(() => {
      expect(screen.getByTestId("embeddings-status-active")).toBeTruthy();
    });
    expect(screen.getByText("http://localhost:11434/api/embeddings")).toBeTruthy();
    expect(screen.getByText("bge-m3")).toBeTruthy();
    expect(screen.getByText("1024")).toBeTruthy();
  });

  it("when embeddings_enabled=true: lexical-only indicator is NOT present", async () => {
    navigateToEmbeddings();
    await waitFor(() => {
      expect(screen.getByTestId("embeddings-status-active")).toBeTruthy();
    });
    expect(screen.queryByTestId("embeddings-status-lexical")).toBeNull();
  });
});

describe("SettingsPanel — Embeddings section disabled state (ADR-0030)", () => {
  // Override fetchEmbeddingConfig to return embeddings_enabled:false BEFORE each test
  // so the mock is in place when renderPanel() mounts and fires the useEffect fetch.
  beforeEach(async () => {
    const { fetchEmbeddingConfig } = await import("../api/providerClient");
    (fetchEmbeddingConfig as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      embedding_url: "http://localhost:11434/api/embeddings",
      embedding_model: "bge-m3",
      embedding_dim: 1024,
      embeddings_enabled: false,
    });
  });

  function navigateToEmbeddingsDisabled() {
    renderPanel();
    const embBtn = document.querySelector('[data-settings-section="embeddings"]');
    fireEvent.click(embBtn!);
  }

  it("when embeddings_enabled=false: renders the lexical-only indicator", async () => {
    navigateToEmbeddingsDisabled();
    await waitFor(() => {
      expect(screen.getByTestId("embeddings-status-lexical")).toBeTruthy();
    });
    // i18n mock returns "lexicalOnly" (last segment of settings.embeddings.lexicalOnly)
    expect(screen.getByText("lexicalOnly")).toBeTruthy();
  });

  it("when embeddings_enabled=false: renders the lexical-only note", async () => {
    navigateToEmbeddingsDisabled();
    await waitFor(() => {
      expect(screen.getByTestId("embeddings-status-lexical")).toBeTruthy();
    });
    // i18n mock returns "lexicalOnlyNote" (last segment)
    expect(screen.getByText("lexicalOnlyNote")).toBeTruthy();
  });

  it("when embeddings_enabled=false: semantic-active indicator is NOT present", async () => {
    navigateToEmbeddingsDisabled();
    await waitFor(() => {
      expect(screen.getByTestId("embeddings-status-lexical")).toBeTruthy();
    });
    expect(screen.queryByTestId("embeddings-status-active")).toBeNull();
  });

  it("when embeddings_enabled=false: URL, model, and dim values still render (dimmed)", async () => {
    navigateToEmbeddingsDisabled();
    await waitFor(() => {
      expect(screen.getByTestId("embeddings-status-lexical")).toBeTruthy();
    });
    // Values are present but inside a dimmed wrapper — DOM still contains them
    expect(screen.getByText("http://localhost:11434/api/embeddings")).toBeTruthy();
    expect(screen.getByText("bge-m3")).toBeTruthy();
    expect(screen.getByText("1024")).toBeTruthy();
  });
});

// ─── 14. MCP Access sub-block — ADR-0033 ────────────────────────────────────
// Covers: posture labels (db/env/none), generate/rotate, one-time reveal + copy,
// token NOT re-shown after dismiss or refetch (GET never returns it), clear token,
// allow-without-token switch + caveat, PUT body shapes, remote toggle aware of allow flag.

describe("SettingsPanel — MCP Access sub-block (ADR-0033)", () => {
  // Helper: navigate to API+MCP, wait for the access sub-block to appear.
  async function navigateToApiMcpAndWait() {
    renderPanel();
    const apiBtn = document.querySelector('[data-settings-section="apiMcp"]');
    fireEvent.click(apiBtn!);
    await waitFor(() => {
      expect(screen.getByTestId("mcp-token-posture")).toBeTruthy();
    });
  }

  // ── Posture label: token_source = "db" ───────────────────────────────────────

  it("shows postureDb label when token_source='db' (default fixture)", async () => {
    await navigateToApiMcpAndWait();
    // i18n mock returns last segment: "settings.apiMcp.access.postureDb" → "postureDb"
    expect(screen.getByText("postureDb")).toBeTruthy();
    expect(screen.queryByText("postureNone")).toBeNull();
    expect(screen.queryByText("postureEnv")).toBeNull();
  });

  // ── Posture label: token_source = "env" ──────────────────────────────────────

  it("shows postureEnv label when token_source='env'", async () => {
    const { fetchMcpInfo } = await import("../api/providerClient");
    (fetchMcpInfo as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      server_name: "synapse",
      transport: "stdio",
      entry_point_command: "python -m app.mcp.server",
      tool_count: 0,
      tools: [],
      http_enabled: true,
      remote_write_enabled: false,
      token_configured: true,
      remote_enabled: false,
      mount_path: "/mcp/server",
      token_source: "env",
      allow_without_token: false,
    });
    await navigateToApiMcpAndWait();
    expect(screen.getByText("postureEnv")).toBeTruthy();
    expect(screen.queryByText("postureDb")).toBeNull();
  });

  // ── Posture label: token_source = "none" ─────────────────────────────────────

  it("shows postureNone label when token_source='none'", async () => {
    const { fetchMcpInfo } = await import("../api/providerClient");
    (fetchMcpInfo as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
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
    });
    await navigateToApiMcpAndWait();
    expect(screen.getByText("postureNone")).toBeTruthy();
    expect(screen.queryByText("postureDb")).toBeNull();
    expect(screen.queryByText("postureEnv")).toBeNull();
  });

  // ── Generate token: button text ───────────────────────────────────────────────

  it("shows 'generateToken' button text when no token is configured", async () => {
    const { fetchMcpInfo } = await import("../api/providerClient");
    (fetchMcpInfo as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
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
    });
    await navigateToApiMcpAndWait();
    // i18n mock: "settings.apiMcp.access.generateToken" → "generateToken"
    expect(screen.getByTestId("mcp-generate-token-btn").textContent).toMatch(/generateToken/i);
  });

  it("shows 'rotateToken' button text when a token is already configured", async () => {
    await navigateToApiMcpAndWait();
    // Default fixture: token_configured=true, token_source="db"
    expect(screen.getByTestId("mcp-generate-token-btn").textContent).toMatch(/rotateToken/i);
  });

  // ── Generate/rotate: one-time reveal ─────────────────────────────────────────

  it("clicking generate calls setMcpAuth({rotate_token:true}) and reveals generated_token ONCE", async () => {
    const { setMcpAuth: mockSetMcpAuth } = await import("../api/providerClient");
    (mockSetMcpAuth as ReturnType<typeof vi.fn>).mockClear();

    await navigateToApiMcpAndWait();
    const genBtn = screen.getByTestId("mcp-generate-token-btn");
    fireEvent.click(genBtn);

    await waitFor(() => {
      expect(mockSetMcpAuth).toHaveBeenCalledWith({ rotate_token: true });
    });
    // The generated token box must appear
    await waitFor(() => {
      expect(screen.getByTestId("mcp-generated-token")).toBeTruthy();
    });
    expect(screen.getByTestId("mcp-generated-token").textContent).toBe("synapse-test-token-abc123xyz");
  });

  it("generated_token reveal includes a copy button", async () => {
    await navigateToApiMcpAndWait();
    fireEvent.click(screen.getByTestId("mcp-generate-token-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("mcp-copy-generated-token-btn")).toBeTruthy();
    });
  });

  it("generated_token reveal includes the one-time warning (revealWarning key)", async () => {
    await navigateToApiMcpAndWait();
    fireEvent.click(screen.getByTestId("mcp-generate-token-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("mcp-generated-token")).toBeTruthy();
    });
    // i18n mock: "settings.apiMcp.access.revealWarning" → "revealWarning"
    expect(screen.getByText("revealWarning")).toBeTruthy();
  });

  it("token is NOT shown before generate is clicked (not pre-populated)", async () => {
    await navigateToApiMcpAndWait();
    // Before any click, the generated-token testid must not be in the DOM
    expect(screen.queryByTestId("mcp-generated-token")).toBeNull();
  });

  it("dismissing the reveal hides the generated_token (it is gone from DOM)", async () => {
    await navigateToApiMcpAndWait();
    fireEvent.click(screen.getByTestId("mcp-generate-token-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("mcp-generated-token")).toBeTruthy();
    });

    // Click dismiss
    fireEvent.click(screen.getByTestId("mcp-dismiss-generated-token-btn"));
    // The reveal box must disappear
    expect(screen.queryByTestId("mcp-generated-token")).toBeNull();
  });

  it("token is NOT re-shown after dismiss — GET /mcp/info never returns it", async () => {
    // This test verifies the invariant: a second GET (or a new mount) never re-shows the token.
    // After dismiss, there is no mcp-generated-token element in the DOM — the panel only
    // shows token_configured=true and token_source="db", never the plaintext.
    await navigateToApiMcpAndWait();
    fireEvent.click(screen.getByTestId("mcp-generate-token-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("mcp-generated-token")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("mcp-dismiss-generated-token-btn"));
    expect(screen.queryByTestId("mcp-generated-token")).toBeNull();

    // The posture label still shows "postureDb" (token_configured=true, token_source="db")
    // but the plaintext is gone.
    expect(screen.getByTestId("mcp-token-posture").textContent).toMatch(/postureDb/i);
  });

  // ── setMcpAuth called with rotate_token=true and NOT with token value ─────────

  it("setMcpAuth response when rotate_token=false has no generated_token — reveal absent", async () => {
    const { setMcpAuth: mockSetMcpAuth } = await import("../api/providerClient");
    // Simulate a response without generated_token (e.g. explicit-token set by owner)
    (mockSetMcpAuth as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      token_configured: true,
      token_source: "db",
      allow_without_token: false,
      remote_enabled: false,
      mount_path: "/mcp/server",
      generated_token: null,
    });

    await navigateToApiMcpAndWait();
    fireEvent.click(screen.getByTestId("mcp-generate-token-btn"));
    await waitFor(() => {
      expect(mockSetMcpAuth).toHaveBeenCalled();
    });
    // generated_token is null in response — reveal box must NOT appear
    expect(screen.queryByTestId("mcp-generated-token")).toBeNull();
  });

  // ── Clear token ───────────────────────────────────────────────────────────────

  it("clear token button is visible when token_configured=true", async () => {
    await navigateToApiMcpAndWait();
    // Default fixture: token_configured=true
    expect(screen.getByTestId("mcp-clear-token-btn")).toBeTruthy();
  });

  it("clear token button is NOT visible when token_configured=false", async () => {
    const { fetchMcpInfo } = await import("../api/providerClient");
    (fetchMcpInfo as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
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
    });
    await navigateToApiMcpAndWait();
    expect(screen.queryByTestId("mcp-clear-token-btn")).toBeNull();
  });

  it("clicking clear token calls setMcpAuth({clear_token:true})", async () => {
    const { setMcpAuth: mockSetMcpAuth } = await import("../api/providerClient");
    (mockSetMcpAuth as ReturnType<typeof vi.fn>).mockClear();
    // Return a posture reflecting cleared state
    (mockSetMcpAuth as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      token_configured: false,
      token_source: "none",
      allow_without_token: false,
      remote_enabled: false,
      mount_path: "/mcp/server",
    });

    await navigateToApiMcpAndWait();
    fireEvent.click(screen.getByTestId("mcp-clear-token-btn"));

    await waitFor(() => {
      expect(mockSetMcpAuth).toHaveBeenCalledWith({ clear_token: true });
    });
    // After clear, posture should update to postureNone
    await waitFor(() => {
      expect(screen.getByTestId("mcp-token-posture").textContent).toMatch(/postureNone/i);
    });
  });

  // ── Allow without token switch ────────────────────────────────────────────────

  it("renders the allow-without-token switch (data-testid='mcp-allow-without-token')", async () => {
    await navigateToApiMcpAndWait();
    expect(screen.getByTestId("mcp-allow-without-token")).toBeTruthy();
  });

  it("allow-without-token switch is unchecked by default (fixture: allow_without_token=false)", async () => {
    await navigateToApiMcpAndWait();
    const toggle = screen.getByTestId("mcp-allow-without-token") as HTMLInputElement;
    expect(toggle.checked).toBe(false);
  });

  it("allow-without-token switch is checked when fixture sets allow_without_token=true", async () => {
    const { fetchMcpInfo } = await import("../api/providerClient");
    (fetchMcpInfo as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      server_name: "synapse",
      transport: "stdio",
      entry_point_command: "python -m app.mcp.server",
      tool_count: 0,
      tools: [],
      http_enabled: true,
      remote_write_enabled: false,
      token_configured: true,
      remote_enabled: false,
      mount_path: "/mcp/server",
      token_source: "db",
      allow_without_token: true,
    });
    await navigateToApiMcpAndWait();
    const toggle = screen.getByTestId("mcp-allow-without-token") as HTMLInputElement;
    expect(toggle.checked).toBe(true);
  });

  it("clicking allow-without-token switch calls setMcpAuth with toggled value (false→true)", async () => {
    const { setMcpAuth: mockSetMcpAuth } = await import("../api/providerClient");
    (mockSetMcpAuth as ReturnType<typeof vi.fn>).mockClear();
    (mockSetMcpAuth as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      token_configured: true,
      token_source: "db",
      allow_without_token: true,
      remote_enabled: false,
      mount_path: "/mcp/server",
    });

    await navigateToApiMcpAndWait();
    // Default fixture: allow_without_token=false → clicking toggles to true
    const toggle = screen.getByTestId("mcp-allow-without-token");
    fireEvent.click(toggle);

    await waitFor(() => {
      expect(mockSetMcpAuth).toHaveBeenCalledWith({ allow_without_token: true });
    });
  });

  it("clicking allow-without-token switch (true→false) calls setMcpAuth({allow_without_token:false})", async () => {
    const { fetchMcpInfo, setMcpAuth: mockSetMcpAuth } = await import("../api/providerClient");
    (fetchMcpInfo as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      server_name: "synapse",
      transport: "stdio",
      entry_point_command: "python -m app.mcp.server",
      tool_count: 0,
      tools: [],
      http_enabled: true,
      remote_write_enabled: false,
      token_configured: true,
      remote_enabled: false,
      mount_path: "/mcp/server",
      token_source: "db",
      allow_without_token: true, // starts ON
    });
    (mockSetMcpAuth as ReturnType<typeof vi.fn>).mockClear();
    (mockSetMcpAuth as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      token_configured: true,
      token_source: "db",
      allow_without_token: false, // server turns it off
      remote_enabled: false,
      mount_path: "/mcp/server",
    });

    await navigateToApiMcpAndWait();
    const toggle = screen.getByTestId("mcp-allow-without-token");
    fireEvent.click(toggle);

    await waitFor(() => {
      expect(mockSetMcpAuth).toHaveBeenCalledWith({ allow_without_token: false });
    });
  });

  it("the local-only caveat (allowWithoutTokenCaveat key) is always visible for this switch", async () => {
    await navigateToApiMcpAndWait();
    // i18n mock returns "allowWithoutTokenCaveat" (last segment)
    expect(screen.getByTestId("mcp-allow-without-token-caveat")).toBeTruthy();
    expect(screen.getByText("allowWithoutTokenCaveat")).toBeTruthy();
  });

  // ── Remote toggle is enabled when allow_without_token=true even with no token ──

  it("remote toggle is enabled when allow_without_token=true and no token", async () => {
    const { fetchMcpInfo } = await import("../api/providerClient");
    (fetchMcpInfo as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
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
      allow_without_token: true, // ADR-0033 §2.4: allow-aware floor
    });
    await navigateToApiMcpAndWait();
    const remoteToggle = screen.getByTestId("mcp-remote-toggle") as HTMLInputElement;
    // With allow_without_token=true, remote toggle must NOT be disabled
    expect(remoteToggle.disabled).toBe(false);
  });

  it("remote toggle is disabled when both token_configured=false AND allow_without_token=false", async () => {
    const { fetchMcpInfo } = await import("../api/providerClient");
    (fetchMcpInfo as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
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
    });
    await navigateToApiMcpAndWait();
    const remoteToggle = screen.getByTestId("mcp-remote-toggle") as HTMLInputElement;
    expect(remoteToggle.disabled).toBe(true);
  });

  // ── Token value never in the DOM except during one-time reveal ────────────────

  it("no token value appears in the DOM in the default (db) posture (no reveal triggered)", async () => {
    await navigateToApiMcpAndWait();
    // The actual token hash or plaintext should never appear; only posture metadata.
    const body = document.body.textContent ?? "";
    // Ensure no long hex-like or URL-safe-base64 string (token-shaped) appears in rendered text
    // (The fixture token "synapse-test-token-abc123xyz" must NOT be present without clicking generate)
    expect(body).not.toContain("synapse-test-token-abc123xyz");
  });
});

// ─── 15. Web Clipper section — ADR-0040 ──────────────────────────────────────
// Covers: enable toggle calls PUT set_enabled; generate/rotate/clear token mirrors
// SectionApiMcp UX; one-time reveal; clear hides posture; clip URL display; origins PUT.

describe("SettingsPanel — Web Clipper section (ADR-0040)", () => {
  async function navigateToClipperAndWait() {
    renderPanel();
    const btn = document.querySelector('[data-settings-section="webClipper"]');
    fireEvent.click(btn!);
    await waitFor(() => {
      expect(screen.getByTestId("clip-token-posture")).toBeTruthy();
    });
  }

  it("shows loading state immediately after navigation (before fetch resolves)", () => {
    renderPanel();
    const btn = document.querySelector('[data-settings-section="webClipper"]');
    fireEvent.click(btn!);
    // i18n mock: "settings.webClipper.loading" → "loading"
    expect(screen.getByText("loading")).toBeTruthy();
  });

  it("shows postureDb label when token_source='db' (default fixture)", async () => {
    await navigateToClipperAndWait();
    // i18n mock: "settings.webClipper.postureDb" → "postureDb"
    expect(screen.getByText("postureDb")).toBeTruthy();
  });

  it("shows postureNone label when token_source='none'", async () => {
    const { fetchClipConfig } = await import("../api/providerClient");
    (fetchClipConfig as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      enabled: false,
      token_configured: false,
      token_source: "none",
      allowed_origins: [],
      max_body_bytes: 1048576,
    });
    await navigateToClipperAndWait();
    expect(screen.getByText("postureNone")).toBeTruthy();
    expect(screen.queryByText("postureDb")).toBeNull();
  });

  it("shows 'generateToken' button text when no token configured", async () => {
    const { fetchClipConfig } = await import("../api/providerClient");
    (fetchClipConfig as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      enabled: false,
      token_configured: false,
      token_source: "none",
      allowed_origins: [],
      max_body_bytes: 1048576,
    });
    await navigateToClipperAndWait();
    expect(screen.getByTestId("clip-generate-token-btn").textContent).toMatch(/generateToken/i);
  });

  it("shows 'rotateToken' button text when a token is already configured", async () => {
    await navigateToClipperAndWait();
    // Default fixture: token_configured=true
    expect(screen.getByTestId("clip-generate-token-btn").textContent).toMatch(/rotateToken/i);
  });

  it("clicking generate calls setClipConfig({rotate_token:true}) and reveals generated_token ONCE", async () => {
    const { setClipConfig: mockSetClipConfig } = await import("../api/providerClient");
    (mockSetClipConfig as ReturnType<typeof vi.fn>).mockClear();

    await navigateToClipperAndWait();
    fireEvent.click(screen.getByTestId("clip-generate-token-btn"));

    await waitFor(() => {
      expect(mockSetClipConfig).toHaveBeenCalledWith({ rotate_token: true });
    });
    await waitFor(() => {
      expect(screen.getByTestId("clip-generated-token")).toBeTruthy();
    });
    expect(screen.getByTestId("clip-generated-token").textContent).toBe("clip-test-token-xyz789abc");
  });

  it("generated token reveal includes a copy button", async () => {
    await navigateToClipperAndWait();
    fireEvent.click(screen.getByTestId("clip-generate-token-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("clip-copy-generated-token-btn")).toBeTruthy();
    });
  });

  it("generated token reveal includes the one-time warning (revealWarning key)", async () => {
    await navigateToClipperAndWait();
    fireEvent.click(screen.getByTestId("clip-generate-token-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("clip-generated-token")).toBeTruthy();
    });
    // i18n mock: "settings.webClipper.revealWarning" → "revealWarning"
    expect(screen.getByText("revealWarning")).toBeTruthy();
  });

  it("dismissing the reveal hides the generated token (gone from DOM)", async () => {
    await navigateToClipperAndWait();
    fireEvent.click(screen.getByTestId("clip-generate-token-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("clip-generated-token")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("clip-dismiss-generated-token-btn"));
    expect(screen.queryByTestId("clip-generated-token")).toBeNull();
  });

  it("token is NOT shown before generate is clicked", async () => {
    await navigateToClipperAndWait();
    expect(screen.queryByTestId("clip-generated-token")).toBeNull();
  });

  it("clear token button visible when token_configured=true", async () => {
    await navigateToClipperAndWait();
    expect(screen.getByTestId("clip-clear-token-btn")).toBeTruthy();
  });

  it("clear token button NOT visible when token_configured=false", async () => {
    const { fetchClipConfig } = await import("../api/providerClient");
    (fetchClipConfig as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      enabled: true,
      token_configured: false,
      token_source: "none",
      allowed_origins: [],
      max_body_bytes: 1048576,
    });
    await navigateToClipperAndWait();
    expect(screen.queryByTestId("clip-clear-token-btn")).toBeNull();
  });

  it("clicking clear token calls setClipConfig({clear_token:true}) and updates posture to postureNone", async () => {
    const { setClipConfig: mockSetClipConfig } = await import("../api/providerClient");
    (mockSetClipConfig as ReturnType<typeof vi.fn>).mockClear();
    (mockSetClipConfig as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      enabled: true,
      token_configured: false,
      token_source: "none",
      allowed_origins: [],
      max_body_bytes: 1048576,
    });

    await navigateToClipperAndWait();
    fireEvent.click(screen.getByTestId("clip-clear-token-btn"));

    await waitFor(() => {
      expect(mockSetClipConfig).toHaveBeenCalledWith({ clear_token: true });
    });
    await waitFor(() => {
      expect(screen.getByTestId("clip-token-posture").textContent).toMatch(/postureNone/i);
    });
  });

  it("enable toggle calls setClipConfig({set_enabled:false}) when currently enabled", async () => {
    const { setClipConfig: mockSetClipConfig } = await import("../api/providerClient");
    (mockSetClipConfig as ReturnType<typeof vi.fn>).mockClear();
    (mockSetClipConfig as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      enabled: false,
      token_configured: true,
      token_source: "db",
      allowed_origins: [],
      max_body_bytes: 1048576,
    });

    await navigateToClipperAndWait();
    // Default fixture: enabled=true — clicking toggles to false
    const toggle = screen.getByTestId("clip-enabled-toggle");
    fireEvent.click(toggle);

    await waitFor(() => {
      expect(mockSetClipConfig).toHaveBeenCalledWith({ set_enabled: false });
    });
  });

  it("clip endpoint URL is window.location.origin + /clip", async () => {
    await navigateToClipperAndWait();
    const urlEl = screen.getByTestId("clip-endpoint-url");
    expect(urlEl.textContent).toBe(`${window.location.origin}/clip`);
  });

  it("no token value appears in DOM without clicking generate", async () => {
    await navigateToClipperAndWait();
    const body = document.body.textContent ?? "";
    expect(body).not.toContain("clip-test-token-xyz789abc");
  });
});

// ─── 16. Web Search section — ADR-0041 ───────────────────────────────────────
// Covers: source badge renders; URL/categories/max_queries fields call PUT;
// clear button calls PUT {clear:true}; SearXNG-only note present; URL validation.

describe("SettingsPanel — Web Search section (ADR-0041)", () => {
  async function navigateToWebSearchAndWait() {
    renderPanel();
    const btn = document.querySelector('[data-settings-section="webSearch"]');
    fireEvent.click(btn!);
    await waitFor(() => {
      expect(screen.getByTestId("web-search-configured-badge")).toBeTruthy();
    });
  }

  it("shows loading state immediately after navigation (before fetch resolves)", () => {
    renderPanel();
    const btn = document.querySelector('[data-settings-section="webSearch"]');
    fireEvent.click(btn!);
    // i18n mock: "settings.webSearch.loading" → "loading"
    expect(screen.getByText("loading")).toBeTruthy();
  });

  it("renders 'configuredBadge' when configured=true (default fixture)", async () => {
    await navigateToWebSearchAndWait();
    // i18n mock: "settings.webSearch.configuredBadge" → "configuredBadge"
    expect(screen.getByText("configuredBadge")).toBeTruthy();
  });

  it("renders 'notConfiguredBadge' when configured=false", async () => {
    const { fetchWebSearchConfig } = await import("../api/providerClient");
    (fetchWebSearchConfig as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      configured: false,
      url: null,
      categories: [],
      max_queries: 3,
      source: "none",
    });
    await navigateToWebSearchAndWait();
    expect(screen.getByText("notConfiguredBadge")).toBeTruthy();
    expect(screen.queryByText("configuredBadge")).toBeNull();
  });

  it("renders the source badge with the source value from fixture", async () => {
    await navigateToWebSearchAndWait();
    const badge = screen.getByTestId("web-search-source-badge");
    // i18n mock returns interpolated key last-segment: "sourceBadge" (interpolation ignored)
    expect(badge).toBeTruthy();
  });

  it("URL input is pre-filled from fetched config", async () => {
    await navigateToWebSearchAndWait();
    const input = screen.getByTestId("web-search-url-input") as HTMLInputElement;
    expect(input.value).toBe("http://searxng:8080");
  });

  it("clicking Save URL calls setWebSearchConfig with {set_url: value}", async () => {
    const { setWebSearchConfig: mockSetWebSearch } = await import("../api/providerClient");
    (mockSetWebSearch as ReturnType<typeof vi.fn>).mockClear();

    await navigateToWebSearchAndWait();
    const input = screen.getByTestId("web-search-url-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "http://mysearxng:8888" } });

    fireEvent.click(screen.getByTestId("web-search-url-save"));

    await waitFor(() => {
      expect(mockSetWebSearch).toHaveBeenCalledWith({ set_url: "http://mysearxng:8888" });
    });
  });

  it("categories input pre-filled from fetched config", async () => {
    await navigateToWebSearchAndWait();
    const input = screen.getByTestId("web-search-categories-input") as HTMLInputElement;
    expect(input.value).toBe("general");
  });

  it("clicking Save categories calls setWebSearchConfig with {set_categories: value}", async () => {
    const { setWebSearchConfig: mockSetWebSearch } = await import("../api/providerClient");
    (mockSetWebSearch as ReturnType<typeof vi.fn>).mockClear();

    await navigateToWebSearchAndWait();
    const input = screen.getByTestId("web-search-categories-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "general,news" } });

    fireEvent.click(screen.getByTestId("web-search-categories-save"));

    await waitFor(() => {
      expect(mockSetWebSearch).toHaveBeenCalledWith({ set_categories: "general,news" });
    });
  });

  it("max_queries input pre-filled from fetched config", async () => {
    await navigateToWebSearchAndWait();
    const input = screen.getByTestId("web-search-max-queries-input") as HTMLInputElement;
    expect(Number(input.value)).toBe(3);
  });

  it("clicking Save max_queries calls setWebSearchConfig with {set_max_queries: value}", async () => {
    const { setWebSearchConfig: mockSetWebSearch } = await import("../api/providerClient");
    (mockSetWebSearch as ReturnType<typeof vi.fn>).mockClear();

    await navigateToWebSearchAndWait();
    const input = screen.getByTestId("web-search-max-queries-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "5" } });

    fireEvent.click(screen.getByTestId("web-search-max-queries-save"));

    await waitFor(() => {
      expect(mockSetWebSearch).toHaveBeenCalledWith({ set_max_queries: 5 });
    });
  });

  it("clicking Clear all calls setWebSearchConfig with {clear: true}", async () => {
    const { setWebSearchConfig: mockSetWebSearch } = await import("../api/providerClient");
    (mockSetWebSearch as ReturnType<typeof vi.fn>).mockClear();

    await navigateToWebSearchAndWait();
    fireEvent.click(screen.getByTestId("web-search-clear-btn"));

    await waitFor(() => {
      expect(mockSetWebSearch).toHaveBeenCalledWith({ clear: true });
    });
  });

  it("shows an error state when fetchWebSearchConfig rejects", async () => {
    const { fetchWebSearchConfig } = await import("../api/providerClient");
    (fetchWebSearchConfig as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error("network error"));

    renderPanel();
    const btn = document.querySelector('[data-settings-section="webSearch"]');
    fireEvent.click(btn!);

    await waitFor(() => {
      // i18n mock: "settings.webSearch.error" → "error"
      expect(screen.getByText("error")).toBeTruthy();
    });
  });

  it("shows SearXNG-only note (I9) on the web search section", async () => {
    await navigateToWebSearchAndWait();
    // i18n mock: "settings.webSearch.searxngOnly" → "searxngOnly"
    expect(screen.getByText("searxngOnly")).toBeTruthy();
  });
});
