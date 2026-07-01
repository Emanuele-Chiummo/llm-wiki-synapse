/**
 * NoteViewRelated.test.tsx — related panel + pagesClient.fetchRelatedPages tests.
 *
 * Coverage:
 *   A. fetchRelatedPages client
 *      A1. Calls GET /pages/{id}/related?limit=10 and returns typed response.
 *      A2. Returns items array + total from the response.
 *      A3. Throws ApiError on non-ok response.
 *
 *   B. NoteView — related panel
 *      B1. Renders related items when API returns non-empty list.
 *      B2. Clicking a related item calls selectPage with that item's page_id.
 *      B3. Panel is hidden (not in DOM) when total === 0.
 *      B4. Error state shows muted error text, does not crash the page.
 *      B5. Each related item shows a type badge when item.type is set.
 *
 *   C. NoteView — type badge prefers data.type (Task B)
 *      C1. When data.type is present, uses it for the badge (not the graph node type).
 *      C2. When data.type is absent/null, falls back to the graph node type.
 *
 *   D. NoteView — sources row (Task B)
 *      D1. When data.sources is non-empty, renders the sources row.
 *      D2. When data.sources is empty/null/absent, sources row is hidden.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import { fireEvent } from "@testing-library/react";
import type { GraphNode, RelatedPagesResponse } from "../api/types";

// ─── Mock CodeMirrorEditor ────────────────────────────────────────────────────

vi.mock("../components/wiki/CodeMirrorEditor", () => ({
  CodeMirrorEditor: ({
    initialContent,
    handleRef,
  }: {
    initialContent: string;
    handleRef: { current: { getContent: () => string } | null };
  }) => {
    handleRef.current = { getContent: () => initialContent };
    return null;
  },
}));

// ─── Mock API clients ─────────────────────────────────────────────────────────

vi.mock("../api/pagesClient", () => ({
  fetchPageContent: vi.fn(),
  savePageContent: vi.fn(),
  fetchPages: vi.fn(),
  fetchStatus: vi.fn(),
  fetchRelatedPages: vi.fn(),
}));

vi.mock("../api/graphClient", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.name = "ApiError";
      this.status = status;
    }
  },
}));

// ─── Mock Toast ───────────────────────────────────────────────────────────────

const mockShowToast = vi.fn();
vi.mock("../components/common/Toast", () => ({
  showToast: (...args: unknown[]) => mockShowToast(...args),
  ToastHost: () => null,
}));

// ─── Mock renderMarkdown ──────────────────────────────────────────────────────

vi.mock("../components/chat/renderMarkdown", () => ({
  renderMarkdown: (raw: string) => `<p>${raw}</p>`,
  stripLeadingFrontmatter: (raw: string) => raw,
}));

// ─── Mock i18n ────────────────────────────────────────────────────────────────

vi.mock("react-i18next", () => {
  const map: Record<string, string> = {
    "noteView.selectPagePrompt": "Select a page to read or edit",
    "noteView.selectPageBody": "Click any page in the tree on the left.",
    "noteView.edit": "Edit",
    "noteView.save": "Save",
    "noteView.cancel": "Cancel",
    "noteView.saved": "Saved",
    "noteView.staleConflict": "This note changed on disk — reload before editing",
    "noteView.reload": "Reload",
    "noteView.loadError": "Could not load page content",
    "noteView.saving": "Saving…",
    "noteView.wikilinkNotFound": "Page not found: {{title}}",
    "noteView.sources": "Sources",
    "noteView.related": "Related ({{count}})",
    "noteView.relatedError": "Could not load related pages",
    "common.loading": "Loading…",
    "common.retry": "Retry",
  };
  const singleton = {
    t: (key: string, vars?: Record<string, string | number>) => {
      let val = map[key] ?? key;
      if (vars) {
        for (const [k, v] of Object.entries(vars)) {
          val = val.replace(`{{${k}}}`, String(v));
        }
      }
      return val;
    },
    i18n: { language: "en" },
  };
  return { useTranslation: () => singleton };
});

// ─── Mock graphStore ──────────────────────────────────────────────────────────

let _selectedNodeId: string | null = "page-abc";
let _nodes: GraphNode[] = [];
const _mockSelectPage = vi.fn();

const _mockSetActiveSection = vi.fn();

vi.mock("../store/graphStore", () => ({
  useGraphStore: (selector: (s: unknown) => unknown) => {
    const store = {
      selectedNodeId: _selectedNodeId,
      nodes: _nodes,
      selectPage: _mockSelectPage,
      setActiveSection: _mockSetActiveSection,
    };
    return selector(store);
  },
  selectSelectedNodeId: (s: { selectedNodeId: string | null }) => s.selectedNodeId,
  selectNodes: (s: { nodes: GraphNode[] }) => s.nodes,
  selectSelectPage: (s: { selectPage: typeof _mockSelectPage }) => s.selectPage,
  selectSetActiveSection: (s: { setActiveSection: typeof _mockSetActiveSection }) => s.setActiveSection,
}));

vi.mock("zustand/react/shallow", () => ({
  useShallow: (fn: unknown) => fn,
}));

// ─── Imports AFTER all mocks ──────────────────────────────────────────────────

import * as pagesClientModule from "../api/pagesClient";
import { ApiError as MockApiError } from "../api/graphClient";
import { NoteView } from "../components/wiki/NoteView";
import type { PageContentResponse } from "../api/types";

const mockedFetchContent = pagesClientModule.fetchPageContent as ReturnType<typeof vi.fn>;
const mockedFetchRelated = pagesClientModule.fetchRelatedPages as ReturnType<typeof vi.fn>;

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const PAGE_CONTENT_BASE: PageContentResponse = {
  id: "page-abc",
  title: "Temperature Scaling",
  file_path: "wiki/concepts/temperature_scaling.md",
  content: "# Temperature Scaling\n\nContent here.",
  content_hash: "sha256-abc123",
  updated_at: "2025-06-30T10:00:00Z",
};

const RELATED_RESPONSE: RelatedPagesResponse = {
  items: [
    { page_id: "page-def", title: "Softmax Function",  type: "concept", score: 8.5 },
    { page_id: "page-ghi", title: "Cross Entropy Loss", type: "concept", score: 6.0 },
    { page_id: "page-jkl", title: "Paper: Hinton 2015", type: null,     score: 3.0 },
  ],
  total: 3,
};

const GRAPH_NODES: GraphNode[] = [
  { id: "page-abc", title: "Temperature Scaling", type: "entity", x: 0, y: 0 },
];

// ─── Helpers ──────────────────────────────────────────────────────────────────

async function renderAndWaitReady() {
  render(<NoteView />);
  await waitFor(() => screen.getByTestId("note-edit-btn"));
}

// ─── A. fetchRelatedPages client ──────────────────────────────────────────────

describe("fetchRelatedPages client (Task A)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("A1: calls GET /pages/{id}/related?limit=10", async () => {
    const mockResp = { items: [], total: 0 };
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: async () => mockResp,
    } as Response);

    // Call the real function (not mocked here — we spy on fetch directly)
    // Import the unmocked client for this section only.
    const { fetchRelatedPages: realFn } =
      await vi.importActual<typeof pagesClientModule>("../api/pagesClient");
    await realFn("page-abc");

    expect(fetchSpy).toHaveBeenCalledWith(
      expect.stringContaining("/pages/page-abc/related?limit=10"),
      expect.anything(),
    );
  });

  it("A2: returns items and total from the response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: async () => RELATED_RESPONSE,
    } as Response);

    const { fetchRelatedPages: realFn } =
      await vi.importActual<typeof pagesClientModule>("../api/pagesClient");
    const result = await realFn("page-abc");

    expect(result.total).toBe(3);
    expect(result.items).toHaveLength(3);
    expect(result.items[0]).toMatchObject({
      page_id: "page-def",
      title: "Softmax Function",
      type: "concept",
      score: 8.5,
    });
  });

  it("A3: throws ApiError on non-ok response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: false,
      status: 404,
      json: async () => ({ detail: "Page not found" }),
    } as Response);

    const { fetchRelatedPages: realFn } =
      await vi.importActual<typeof pagesClientModule>("../api/pagesClient");
    await expect(realFn("page-unknown")).rejects.toThrow("404");
  });
});

// ─── B. NoteView related panel ────────────────────────────────────────────────

describe("NoteView — related panel (Task C)", () => {
  beforeEach(() => {
    _selectedNodeId = "page-abc";
    _nodes = GRAPH_NODES;
    vi.clearAllMocks();
    mockedFetchContent.mockResolvedValue(PAGE_CONTENT_BASE);
  });

  afterEach(() => {
    _selectedNodeId = null;
    _nodes = [];
  });

  // B1: renders items when API returns a non-empty list

  it("B1: renders related items when API returns non-empty list", async () => {
    mockedFetchRelated.mockResolvedValue(RELATED_RESPONSE);

    await renderAndWaitReady();

    await waitFor(() => {
      expect(screen.getByTestId("related-panel")).toBeDefined();
      expect(screen.getByTestId("related-list")).toBeDefined();
    });

    // All three items are rendered
    expect(screen.getByTestId("related-item-page-def")).toBeDefined();
    expect(screen.getByTestId("related-item-page-ghi")).toBeDefined();
    expect(screen.getByTestId("related-item-page-jkl")).toBeDefined();

    // Titles are visible
    expect(screen.getByText("Softmax Function")).toBeDefined();
    expect(screen.getByText("Cross Entropy Loss")).toBeDefined();
  });

  // B2: clicking a related item calls selectPage

  it("B2: clicking a related item calls selectPage with that item's page_id", async () => {
    mockedFetchRelated.mockResolvedValue(RELATED_RESPONSE);

    await renderAndWaitReady();
    await waitFor(() => screen.getByTestId("related-item-page-def"));

    act(() => {
      fireEvent.click(screen.getByTestId("related-item-page-def"));
    });

    expect(_mockSelectPage).toHaveBeenCalledWith("page-def", "tree");
  });

  // B3: panel hidden when total === 0

  it("B3: related panel is absent when total === 0", async () => {
    mockedFetchRelated.mockResolvedValue({ items: [], total: 0 });

    await renderAndWaitReady();

    // Wait for the related fetch to settle
    await waitFor(() => {
      // The panel should not be in the DOM at all
      expect(screen.queryByTestId("related-panel")).toBeNull();
    });
  });

  // B4: error state shows muted text, page still renders

  it("B4: quiet error state does not crash the page", async () => {
    mockedFetchRelated.mockRejectedValue(new MockApiError(500, "Internal error"));

    await renderAndWaitReady();

    await waitFor(() => {
      expect(screen.getByTestId("related-error")).toBeDefined();
    });

    // The page title is still visible — related error is non-blocking
    expect(screen.getByText("Temperature Scaling")).toBeDefined();
  });

  // B5: item type badge rendered when item.type is set

  it("B5: type badge rendered for related items that have a type", async () => {
    mockedFetchRelated.mockResolvedValue(RELATED_RESPONSE);

    await renderAndWaitReady();
    await waitFor(() => screen.getByTestId("related-item-page-def"));

    // "Softmax Function" has type "concept" — we verify by looking at item row content
    const softmaxItem = screen.getByTestId("related-item-page-def");
    expect(softmaxItem.textContent).toContain("concept");

    // "Paper: Hinton 2015" has type null — no badge text for it
    const hintonItem = screen.getByTestId("related-item-page-jkl");
    expect(hintonItem.textContent).not.toContain("concept");
    expect(hintonItem.textContent).not.toContain("entity");
  });
});

// ─── C. Type badge prefers data.type (Task B) ─────────────────────────────────

describe("NoteView — type badge preference (Task B)", () => {
  beforeEach(() => {
    _selectedNodeId = "page-abc";
    vi.clearAllMocks();
    mockedFetchRelated.mockResolvedValue({ items: [], total: 0 });
  });

  afterEach(() => {
    _selectedNodeId = null;
    _nodes = [];
  });

  it("C1: uses data.type from content response when present (not graph node type)", async () => {
    // graph node type = "entity", data.type = "concept" → badge should say "concept"
    _nodes = [{ id: "page-abc", title: "Temperature Scaling", type: "entity", x: 0, y: 0 }];
    mockedFetchContent.mockResolvedValue({ ...PAGE_CONTENT_BASE, type: "concept" });

    await renderAndWaitReady();
    await waitFor(() => screen.getByTestId("note-type-badge"));

    expect(screen.getByTestId("note-type-badge").textContent).toBe("concept");
  });

  it("C2: falls back to graph node type when data.type is absent", async () => {
    // data.type not in response → fall back to graph node type "entity"
    _nodes = [{ id: "page-abc", title: "Temperature Scaling", type: "entity", x: 0, y: 0 }];
    // PAGE_CONTENT_BASE has no type field (undefined) → should fall back
    mockedFetchContent.mockResolvedValue(PAGE_CONTENT_BASE);

    await renderAndWaitReady();
    await waitFor(() => screen.getByTestId("note-type-badge"));

    expect(screen.getByTestId("note-type-badge").textContent).toBe("entity");
  });

  it("C2b: falls back to graph node type when data.type is null", async () => {
    _nodes = [{ id: "page-abc", title: "Temperature Scaling", type: "synthesis", x: 0, y: 0 }];
    mockedFetchContent.mockResolvedValue({ ...PAGE_CONTENT_BASE, type: null });

    await renderAndWaitReady();
    await waitFor(() => screen.getByTestId("note-type-badge"));

    expect(screen.getByTestId("note-type-badge").textContent).toBe("synthesis");
  });
});

// ─── D. Sources row (Task B) ──────────────────────────────────────────────────

describe("NoteView — sources row (Task B)", () => {
  beforeEach(() => {
    _selectedNodeId = "page-abc";
    _nodes = [];
    vi.clearAllMocks();
    mockedFetchRelated.mockResolvedValue({ items: [], total: 0 });
  });

  afterEach(() => {
    _selectedNodeId = null;
    _nodes = [];
  });

  it("D1: renders sources when data.sources is non-empty", async () => {
    mockedFetchContent.mockResolvedValue({
      ...PAGE_CONTENT_BASE,
      type: "concept",
      sources: ["raw/sources/doc1.pdf", "raw/sources/doc2.md"],
    });

    await renderAndWaitReady();
    await waitFor(() => screen.getByTestId("note-sources"));

    const sourcesEl = screen.getByTestId("note-sources");
    expect(sourcesEl.textContent).toContain("raw/sources/doc1.pdf");
    expect(sourcesEl.textContent).toContain("raw/sources/doc2.md");
  });

  it("D2: sources row absent when data.sources is null", async () => {
    mockedFetchContent.mockResolvedValue({
      ...PAGE_CONTENT_BASE,
      sources: null,
    });

    await renderAndWaitReady();

    // No meta row at all when both type and sources are absent
    expect(screen.queryByTestId("note-sources")).toBeNull();
  });

  it("D2b: sources row absent when data.sources is empty array", async () => {
    mockedFetchContent.mockResolvedValue({
      ...PAGE_CONTENT_BASE,
      sources: [],
    });

    await renderAndWaitReady();

    expect(screen.queryByTestId("note-sources")).toBeNull();
  });
});
