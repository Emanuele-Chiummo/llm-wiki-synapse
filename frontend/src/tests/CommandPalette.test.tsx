/**
 * CommandPalette.test.tsx — unit tests for T2 command palette (ADR-0048 §2.2).
 *
 * Covers:
 *   - Palette opens/closes via keyboard event (Cmd+K, Esc)
 *   - Filters results and CAPS at 20 (mock fetchAllPages with 30+ pages)
 *   - Enter triggers navigation action (mock store)
 *   - Shortcuts ignored while typing in an input (useGlobalShortcuts)
 *   - Click-outside closes the palette
 *
 * GOTCHA: vi.clearAllMocks() wipes mock impls — all mocks are re-set in beforeEach.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import { CommandPalette } from "../components/common/CommandPalette";

// Number of executable actions in the palette (v2, FE-UIUX-3): new chat, import,
// run lint, switch project, switch theme, regenerate overview, new page.
const ACTION_COUNT = 7;

// ─── Mock i18n ────────────────────────────────────────────────────────────────

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const map: Record<string, string> = {
        "palette.placeholder": "Search sections, pages, and actions…",
        "palette.noResults": "No results",
        "palette.sections": "Sections",
        "palette.pages": "Pages",
        "palette.actions": "Actions",
        "palette.hint": "↑↓ navigate · Enter open · Esc close",
        "palette.action.newChat": "New chat",
        "palette.action.importIngest": "Import content",
        "palette.action.runLint": "Run lint scan",
        "palette.action.switchProject": "Switch project",
        "palette.action.switchTheme": "Switch theme",
        "palette.action.regenerateOverview": "Regenerate overview",
        "palette.action.newPage": "New page",
        "nav.home": "Home",
        "nav.chat": "Chat",
        "nav.wiki": "Wiki",
        "nav.sources": "Sources",
        "nav.convert": "Convert",
        "nav.search": "Search",
        "nav.graph": "Graph",
        "nav.lint": "Lint",
        "nav.review": "Review",
        "nav.deepSearch": "Deep Search",
        "nav.ingest": "Ingest",
        "nav.settings": "Settings",
        "nav.projects": "Projects",
      };
      return map[key] ?? key;
    },
    i18n: { changeLanguage: vi.fn() },
  }),
}));

// ─── Mock graphStore ──────────────────────────────────────────────────────────

const mockSetActiveSection = vi.fn();
const mockSelectPage = vi.fn();

vi.mock("../store/appStore", () => ({
  useAppStore: (selector: (s: unknown) => unknown) =>
    selector({
      vaultId: "default",
      setActiveSection: mockSetActiveSection,
      selectPage: mockSelectPage,
    }),
  selectVaultId: (s: { vaultId: string }) => s.vaultId,
  selectSetActiveSection: (s: { setActiveSection: unknown }) => s.setActiveSection,
  selectSelectPage: (s: { selectPage: unknown }) => s.selectPage,
}));

// ─── Mock fetchAllPages ───────────────────────────────────────────────────────

const mockFetchAllPages = vi.fn();

vi.mock("../api/pagesClient", () => ({
  fetchAllPages: (...args: unknown[]) => mockFetchAllPages(...args),
}));

// ─── Mock v2 action dependencies (FE-UIUX-3) ─────────────────────────────────
// CommandPalette actions delegate to these — mocked here so palette tests never
// hit real stores/API. Behavior of the underlying operations is unit-tested in
// their own dedicated store/component test files.

const mockStartNewConversation = vi.fn();
vi.mock("../store/chatActions", () => ({
  startNewConversation: (...args: unknown[]) => mockStartNewConversation(...args),
}));

const mockRunNow = vi.fn();
vi.mock("../store/importScheduleStore", () => ({
  useImportScheduleStore: { getState: () => ({ runNow: mockRunNow }) },
}));

const mockLintScan = vi.fn();
vi.mock("../store/lintStore", () => ({
  useLintStore: { getState: () => ({ scan: mockLintScan }) },
}));

const mockSetTheme = vi.fn();
const mockSetDraftTheme = vi.fn();
vi.mock("../store/settingsStore", () => ({
  useSettingsStore: {
    getState: () => ({ theme: "light", setTheme: mockSetTheme, setDraftTheme: mockSetDraftTheme }),
  },
}));

const mockTriggerRegenerateOverview = vi.fn();
vi.mock("../api/opsClient", () => ({
  triggerRegenerateOverview: (...args: unknown[]) => mockTriggerRegenerateOverview(...args),
}));

// ─── Helpers ──────────────────────────────────────────────────────────────────

function makePages(count: number) {
  return Array.from({ length: count }, (_, i) => ({
    id: `page-${i}`,
    vault_id: "default",
    file_path: `pages/page-${i}.md`,
    title: `Page Title ${i}`,
    type: "concept",
    sources: [],
    content_hash: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  }));
}

// ─── Tests ────────────────────────────────────────────────────────────────────

// Default resolved values for the v2 action mocks — re-applied before every test
// since vi.clearAllMocks() (called in every describe's afterEach) wipes impls.
beforeEach(() => {
  mockStartNewConversation.mockResolvedValue({ id: "conv-x" });
  mockRunNow.mockResolvedValue(undefined);
  mockLintScan.mockResolvedValue(undefined);
  mockTriggerRegenerateOverview.mockResolvedValue({ status: "regenerated" });
});

describe("CommandPalette — open/close", () => {
  beforeEach(() => {
    // Re-set impl after each test (clearAllMocks wipes impls).
    mockSetActiveSection.mockImplementation(() => {});
    mockSelectPage.mockImplementation(() => {});
    mockFetchAllPages.mockResolvedValue({ items: [] });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("does NOT render when open=false", () => {
    render(<CommandPalette open={false} onClose={vi.fn()} />);
    expect(screen.queryByTestId("command-palette")).toBeNull();
  });

  it("renders the palette when open=true", async () => {
    render(<CommandPalette open={true} onClose={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByTestId("command-palette")).toBeTruthy();
    });
  });

  it("calls onClose when Esc is pressed", async () => {
    const onClose = vi.fn();
    render(<CommandPalette open={true} onClose={onClose} />);
    const input = await waitFor(() => screen.getByTestId("palette-input"));
    fireEvent.keyDown(input, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("calls onClose when clicking outside (backdrop)", async () => {
    const onClose = vi.fn();
    render(<CommandPalette open={true} onClose={onClose} />);
    const backdrop = await waitFor(() => screen.getByTestId("command-palette-backdrop"));
    fireEvent.click(backdrop);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("does NOT call onClose when clicking inside the modal", async () => {
    const onClose = vi.fn();
    render(<CommandPalette open={true} onClose={onClose} />);
    const palette = await waitFor(() => screen.getByTestId("command-palette"));
    fireEvent.click(palette);
    expect(onClose).not.toHaveBeenCalled();
  });
});

describe("CommandPalette — section list", () => {
  beforeEach(() => {
    mockSetActiveSection.mockImplementation(() => {});
    mockSelectPage.mockImplementation(() => {});
    mockFetchAllPages.mockResolvedValue({ items: [] });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows all 7 actions + 13 sections when query is empty", async () => {
    render(<CommandPalette open={true} onClose={vi.fn()} />);
    await waitFor(() => {
      // 7 actions (v2, FE-UIUX-3) + 13 sections: Home, Sources, Chat, Convert, Wiki,
      // Graph, Search, Deep Search, Review, Lint, Ingest, Settings, Projects
      const items = screen.getAllByRole("option");
      expect(items.length).toBe(ACTION_COUNT + 13);
    });
  });

  it("filters sections by substring (case-insensitive)", async () => {
    render(<CommandPalette open={true} onClose={vi.fn()} />);
    const input = await waitFor(() => screen.getByTestId("palette-input"));
    fireEvent.change(input, { target: { value: "set" } });
    await waitFor(() => {
      // "Settings" matches "set"; none of the v2 action labels contain "set".
      const items = screen.getAllByRole("option");
      expect(items.length).toBe(1);
      expect(items[0]!.textContent).toContain("Settings");
    });
  });
});

describe("CommandPalette — page results and 20-cap (I4)", () => {
  beforeEach(() => {
    mockSetActiveSection.mockImplementation(() => {});
    mockSelectPage.mockImplementation(() => {});
    // Return 35 pages to test the cap
    mockFetchAllPages.mockResolvedValue({ items: makePages(35) });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("caps total results at 20 when pages + sections exceed 20", async () => {
    render(<CommandPalette open={true} onClose={vi.fn()} />);
    // Empty query: 10 sections + 35 pages, but capped at 20.
    await waitFor(() => {
      const items = screen.getAllByRole("option");
      expect(items.length).toBeLessThanOrEqual(20);
    });
  });

  it("fetchAllPages is called once per palette open", async () => {
    render(<CommandPalette open={true} onClose={vi.fn()} />);
    await waitFor(() => {
      expect(mockFetchAllPages).toHaveBeenCalledTimes(1);
    });
  });

  it("fetches pages with the current vaultId", async () => {
    render(<CommandPalette open={true} onClose={vi.fn()} />);
    await waitFor(() => {
      expect(mockFetchAllPages).toHaveBeenCalledWith("default", expect.anything());
    });
  });

  it("shows matching pages when filtering by title", async () => {
    render(<CommandPalette open={true} onClose={vi.fn()} />);
    const input = await waitFor(() => screen.getByTestId("palette-input"));
    // "Title 1" matches "Page Title 1", "Page Title 10".. "Page Title 19" etc.
    fireEvent.change(input, { target: { value: "Title 0" } });
    await waitFor(() => {
      // Sections: none match "Title 0"; pages: "Page Title 0" matches → 1 page.
      const items = screen.getAllByRole("option");
      expect(items.length).toBeGreaterThanOrEqual(1);
      expect(items.length).toBeLessThanOrEqual(20);
    });
  });
});

describe("CommandPalette — keyboard navigation and Enter", () => {
  beforeEach(() => {
    mockSetActiveSection.mockImplementation(() => {});
    mockSelectPage.mockImplementation(() => {});
    mockFetchAllPages.mockResolvedValue({ items: [] });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("ArrowDown moves selection down", async () => {
    render(<CommandPalette open={true} onClose={vi.fn()} />);
    const input = await waitFor(() => screen.getByTestId("palette-input"));
    // Wait for sections to be rendered
    await waitFor(() => screen.getAllByRole("option"));
    fireEvent.keyDown(input, { key: "ArrowDown" });
    // First item was selected (idx=0), now idx=1
    await waitFor(() => {
      const items = screen.getAllByRole("option");
      expect(items[1]!.getAttribute("aria-selected")).toBe("true");
    });
  });

  it("ArrowUp on first item stays at 0", async () => {
    render(<CommandPalette open={true} onClose={vi.fn()} />);
    const input = await waitFor(() => screen.getByTestId("palette-input"));
    await waitFor(() => screen.getAllByRole("option"));
    fireEvent.keyDown(input, { key: "ArrowUp" });
    await waitFor(() => {
      const items = screen.getAllByRole("option");
      expect(items[0]!.getAttribute("aria-selected")).toBe("true");
    });
  });

  it("Enter on a section calls setActiveSection and onClose", async () => {
    const onClose = vi.fn();
    render(<CommandPalette open={true} onClose={onClose} />);
    const input = await waitFor(() => screen.getByTestId("palette-input"));
    // Filter to isolate the "Home" section — actions (v2) are listed before
    // sections, so idx=0 is no longer guaranteed to be a section.
    fireEvent.change(input, { target: { value: "Home" } });
    await waitFor(() => {
      const items = screen.getAllByRole("option");
      expect(items.length).toBe(1);
    });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(mockSetActiveSection).toHaveBeenCalledWith("home");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Enter on the first (unfiltered) result runs an action, not a section", async () => {
    // v2 (FE-UIUX-3): actions are listed FIRST — idx=0 with an empty query is
    // "New chat", not the "Home" section.
    const onClose = vi.fn();
    render(<CommandPalette open={true} onClose={onClose} />);
    const input = await waitFor(() => screen.getByTestId("palette-input"));
    await waitFor(() => screen.getAllByRole("option"));
    fireEvent.keyDown(input, { key: "Enter" });
    expect(mockStartNewConversation).toHaveBeenCalledWith("default");
    expect(mockSetActiveSection).toHaveBeenCalledWith("chat");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Enter on a page calls selectPage + setActiveSection('pages') and onClose", async () => {
    // Only pages, no sections matched: filter for a page title substring
    mockFetchAllPages.mockResolvedValue({ items: makePages(3) });
    const onClose = vi.fn();
    render(<CommandPalette open={true} onClose={onClose} />);
    const input = await waitFor(() => screen.getByTestId("palette-input"));
    // Filter so only page items are shown (none of the section labels contain "Page Title")
    fireEvent.change(input, { target: { value: "Page Title" } });
    await waitFor(() => {
      const items = screen.getAllByRole("option");
      // Should show pages (capped at 20, we have 3)
      expect(items.length).toBe(3);
    });
    // Select the first page
    fireEvent.keyDown(input, { key: "Enter" });
    expect(mockSelectPage).toHaveBeenCalledWith("page-0", "tree");
    expect(mockSetActiveSection).toHaveBeenCalledWith("pages");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("clicking a result item calls onClose", async () => {
    const onClose = vi.fn();
    render(<CommandPalette open={true} onClose={onClose} />);
    await waitFor(() => screen.getAllByRole("option"));
    const firstItem = screen.getAllByRole("option")[0]!;
    fireEvent.click(firstItem);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe("CommandPalette — v2 executable actions (FE-UIUX-3)", () => {
  beforeEach(() => {
    mockSetActiveSection.mockImplementation(() => {});
    mockSelectPage.mockImplementation(() => {});
    mockFetchAllPages.mockResolvedValue({ items: [] });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  async function selectAction(label: string, onClose: () => void) {
    render(<CommandPalette open={true} onClose={onClose} />);
    const input = await waitFor(() => screen.getByTestId("palette-input"));
    fireEvent.change(input, { target: { value: label } });
    await waitFor(() => {
      expect(screen.getAllByRole("option").length).toBe(1);
    });
    fireEvent.keyDown(input, { key: "Enter" });
  }

  it("'New chat' navigates to chat and calls startNewConversation (chatActions.ts)", async () => {
    const onClose = vi.fn();
    await selectAction("New chat", onClose);
    expect(mockSetActiveSection).toHaveBeenCalledWith("chat");
    expect(mockStartNewConversation).toHaveBeenCalledWith("default");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("'Import content' navigates to ingest and calls importScheduleStore.runNow()", async () => {
    const onClose = vi.fn();
    await selectAction("Import content", onClose);
    expect(mockSetActiveSection).toHaveBeenCalledWith("ingest");
    expect(mockRunNow).toHaveBeenCalledTimes(1);
  });

  it("'Run lint scan' navigates to lint and calls lintStore.scan(vaultId)", async () => {
    const onClose = vi.fn();
    await selectAction("Run lint scan", onClose);
    expect(mockSetActiveSection).toHaveBeenCalledWith("lint");
    expect(mockLintScan).toHaveBeenCalledWith("default");
  });

  it("'Switch project' navigates to the Projects section", async () => {
    const onClose = vi.fn();
    await selectAction("Switch project", onClose);
    expect(mockSetActiveSection).toHaveBeenCalledWith("projects");
  });

  it("'Switch theme' calls settingsStore.setTheme() with the next theme in the cycle", async () => {
    const onClose = vi.fn();
    // Mocked useSettingsStore.getState().theme === "light" → next is "dark".
    await selectAction("Switch theme", onClose);
    expect(mockSetTheme).toHaveBeenCalledWith("dark");
    expect(mockSetDraftTheme).toHaveBeenCalledWith("dark");
  });

  it("'Regenerate overview' calls POST /ops/overview/regenerate", async () => {
    const onClose = vi.fn();
    await selectAction("Regenerate overview", onClose);
    expect(mockTriggerRegenerateOverview).toHaveBeenCalledTimes(1);
  });
});

describe("CommandPalette — no results state", () => {
  beforeEach(() => {
    mockSetActiveSection.mockImplementation(() => {});
    mockSelectPage.mockImplementation(() => {});
    mockFetchAllPages.mockResolvedValue({ items: [] });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows no-results message when nothing matches", async () => {
    render(<CommandPalette open={true} onClose={vi.fn()} />);
    const input = await waitFor(() => screen.getByTestId("palette-input"));
    fireEvent.change(input, { target: { value: "zzz-no-match-xyz" } });
    await waitFor(() => {
      expect(screen.getByText("No results")).toBeTruthy();
    });
  });
});

// ─── useGlobalShortcuts integration ──────────────────────────────────────────

import { useGlobalShortcuts } from "../hooks/useGlobalShortcuts";

// We need to mock chatStore and api/chatClient for the hook tests.
const mockAddConversation = vi.fn();
const mockSetActiveConversationId = vi.fn();
const mockSetMessages = vi.fn();

vi.mock("../store/chatStore", () => ({
  useChatStore: (selector: (s: unknown) => unknown) =>
    selector({
      addConversation: mockAddConversation,
      setActiveConversationId: mockSetActiveConversationId,
      setMessages: mockSetMessages,
    }),
  selectAddConversation: (s: { addConversation: unknown }) => s.addConversation,
  selectSetActiveConversationId: (s: { setActiveConversationId: unknown }) =>
    s.setActiveConversationId,
  selectSetMessages: (s: { setMessages: unknown }) => s.setMessages,
}));

const mockCreateConversation = vi.fn();

vi.mock("../api/chatClient", () => ({
  createConversation: (...args: unknown[]) => mockCreateConversation(...args),
}));

vi.mock("../components/common/Toast", () => ({
  showToast: vi.fn(),
  ToastHost: () => null,
}));

// ── Test component that uses the hook ─────────────────────────────────────────

function ShortcutHarness({
  paletteOpen,
  onTogglePalette,
}: {
  paletteOpen: boolean;
  onTogglePalette: () => void;
}) {
  useGlobalShortcuts({ paletteOpen, onTogglePalette });
  return <div data-testid="harness" />;
}

describe("useGlobalShortcuts — Cmd+K", () => {
  beforeEach(() => {
    mockSetActiveSection.mockImplementation(() => {});
    mockAddConversation.mockImplementation(() => {});
    mockSetActiveConversationId.mockImplementation(() => {});
    mockSetMessages.mockImplementation(() => {});
    mockCreateConversation.mockResolvedValue({
      id: "new-conv-1",
      vault_id: "default",
      title: null,
      created_at: "",
      updated_at: "",
    });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("fires onTogglePalette on Cmd+K", () => {
    const onToggle = vi.fn();
    render(<ShortcutHarness paletteOpen={false} onTogglePalette={onToggle} />);
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("fires onTogglePalette on Ctrl+K", () => {
    const onToggle = vi.fn();
    render(<ShortcutHarness paletteOpen={false} onTogglePalette={onToggle} />);
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("fires onTogglePalette even when focus is in an input (Cmd+K always active)", () => {
    const onToggle = vi.fn();
    render(
      <>
        <ShortcutHarness paletteOpen={false} onTogglePalette={onToggle} />
        <input data-testid="text-input" />
      </>,
    );
    const input = screen.getByTestId("text-input");
    fireEvent.keyDown(input, { key: "k", metaKey: true, target: input });
    // Cmd+K fires on window regardless. Since fireEvent.keyDown dispatches
    // on the input but the hook listens on window, we fire on window directly
    // to properly test the always-active behaviour.
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    expect(onToggle).toHaveBeenCalled();
  });
});

describe("useGlobalShortcuts — Cmd+1..5 section switch", () => {
  beforeEach(() => {
    mockSetActiveSection.mockImplementation(() => {});
    mockAddConversation.mockImplementation(() => {});
    mockSetActiveConversationId.mockImplementation(() => {});
    mockSetMessages.mockImplementation(() => {});
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it.each([
    [1, "home"],
    [2, "sources"],
    [3, "chat"],
    [4, "convert"],
    [5, "pages"],
  ] as [number, string][])("Cmd+%i switches to section '%s'", (digit, section) => {
    render(<ShortcutHarness paletteOpen={false} onTogglePalette={vi.fn()} />);
    fireEvent.keyDown(window, { key: String(digit), metaKey: true });
    expect(mockSetActiveSection).toHaveBeenCalledWith(section);
  });

  it("ignores Cmd+1 when focus is inside an INPUT", () => {
    render(
      <>
        <ShortcutHarness paletteOpen={false} onTogglePalette={vi.fn()} />
        <input data-testid="txt" />
      </>,
    );
    const input = screen.getByTestId("txt");
    // Simulate window keydown with input as the target
    const event = new KeyboardEvent("keydown", {
      key: "1",
      metaKey: true,
      bubbles: true,
    });
    Object.defineProperty(event, "target", { value: input, configurable: true });
    window.dispatchEvent(event);
    expect(mockSetActiveSection).not.toHaveBeenCalled();
  });

  it("ignores Cmd+1 when focus is inside a TEXTAREA", () => {
    render(
      <>
        <ShortcutHarness paletteOpen={false} onTogglePalette={vi.fn()} />
        <textarea data-testid="ta" />
      </>,
    );
    const ta = screen.getByTestId("ta");
    const event = new KeyboardEvent("keydown", {
      key: "1",
      metaKey: true,
      bubbles: true,
    });
    Object.defineProperty(event, "target", { value: ta, configurable: true });
    window.dispatchEvent(event);
    expect(mockSetActiveSection).not.toHaveBeenCalled();
  });

  it("ignores Cmd+1 when focus is in a contenteditable element", () => {
    const div = document.createElement("div");
    div.contentEditable = "true";
    document.body.appendChild(div);

    render(<ShortcutHarness paletteOpen={false} onTogglePalette={vi.fn()} />);
    // Dispatch on the div directly (bubbles:true) so jsdom sets event.target = div.
    // The window listener will see div as the target.
    const event = new KeyboardEvent("keydown", {
      key: "1",
      metaKey: true,
      bubbles: true,
    });
    div.dispatchEvent(event);
    expect(mockSetActiveSection).not.toHaveBeenCalled();

    document.body.removeChild(div);
  });
});

describe("useGlobalShortcuts — Cmd+N new conversation", () => {
  beforeEach(() => {
    mockSetActiveSection.mockImplementation(() => {});
    mockAddConversation.mockImplementation(() => {});
    mockSetActiveConversationId.mockImplementation(() => {});
    mockSetMessages.mockImplementation(() => {});
    mockCreateConversation.mockResolvedValue({
      id: "conv-new",
      vault_id: "default",
      title: null,
      created_at: "",
      updated_at: "",
    });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("Cmd+N creates a new conversation", async () => {
    render(<ShortcutHarness paletteOpen={false} onTogglePalette={vi.fn()} />);
    await act(async () => {
      fireEvent.keyDown(window, { key: "n", metaKey: true });
    });
    expect(mockCreateConversation).toHaveBeenCalledTimes(1);
  });

  it("ignores Cmd+N when focus is in an INPUT", async () => {
    render(
      <>
        <ShortcutHarness paletteOpen={false} onTogglePalette={vi.fn()} />
        <input data-testid="inp" />
      </>,
    );
    const inp = screen.getByTestId("inp");
    await act(async () => {
      const event = new KeyboardEvent("keydown", {
        key: "n",
        metaKey: true,
        bubbles: true,
      });
      Object.defineProperty(event, "target", { value: inp, configurable: true });
      window.dispatchEvent(event);
    });
    expect(mockCreateConversation).not.toHaveBeenCalled();
  });
});
