/**
 * NoteViewWikilink.test.tsx — wikilink click→select resolution tests for NoteView (Task A).
 *
 * Coverage:
 *   1. Clicking a .wikilink anchor whose title matches a graph node calls selectPage with
 *      that node's id (case-insensitive title match, first match wins).
 *   2. Clicking a .wikilink anchor whose title has NO match shows the "wikilinkNotFound" toast.
 *   3. Clicking a regular <a> (no .wikilink class, no data-wikilink) does NOT call selectPage.
 *   4. Type badge is rendered when the selected node has a type.
 *   5. Type badge is NOT rendered when the selected node has type = null.
 *
 * The graphStore mock here extends the one in NoteView.test.tsx to expose nodes +
 * selectPage. Both test files mock the same module path so they must be run in
 * separate test workers (Vitest default: each file is isolated).
 *
 * CodeMirror and API mocks reuse the same patterns as NoteView.test.tsx.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act, fireEvent } from "@testing-library/react";
import type { GraphNode } from "../api/types";

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
  fetchAllPages: vi.fn().mockResolvedValue({ items: [] }),
  fetchStatus: vi.fn(),
  fetchRelatedPages: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  fetchPageBySlug: vi.fn().mockRejectedValue(new Error("not found")),
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

// ─── Mock Toast (capture calls) ───────────────────────────────────────────────

const mockShowToast = vi.fn();
vi.mock("../components/common/Toast", () => ({
  showToast: (...args: unknown[]) => mockShowToast(...args),
  ToastHost: () => null,
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
    t: (key: string, vars?: Record<string, string>) => {
      let val = map[key] ?? key;
      if (vars) {
        for (const [k, v] of Object.entries(vars)) {
          val = val.replace(`{{${k}}}`, v);
        }
      }
      return val;
    },
    i18n: { language: "en" },
  };
  return { useTranslation: () => singleton };
});

// ─── Mock renderMarkdown ──────────────────────────────────────────────────────
// Returns HTML that contains a .wikilink anchor so we can fire click events on it.

vi.mock("../components/chat/renderMarkdown", () => ({
  renderMarkdown: (_raw: string) =>
    '<p>See <a class="wikilink" data-wikilink="Temperature Scaling">Temperature Scaling</a> here.</p>',
  stripLeadingFrontmatter: (raw: string) => raw,
}));

// ─── Mock graphStore ──────────────────────────────────────────────────────────
// Extended mock with nodes array and selectPage action.

let _selectedNodeId: string | null = "page-abc";
let _nodes: GraphNode[] = [];
const _mockSelectPage = vi.fn();

const _mockSetActiveSection = vi.fn();

vi.mock("../store/graphStore", () => ({
  useGraphStore: (selector: (s: unknown) => unknown) => {
    // Replicate the store shape accessed by NoteView
    const store = { nodes: _nodes, edges: [] };
    return selector(store);
  },
  selectNodes: (s: { nodes: GraphNode[] }) => s.nodes,
  selectEdges: (s: { edges: [] }) => s.edges,
}));

vi.mock("../store/appStore", () => ({
  useAppStore: (selector: (s: unknown) => unknown) => {
    const store = {
      selectedNodeId: _selectedNodeId,
      selectPage: _mockSelectPage,
      setActiveSection: _mockSetActiveSection,
    };
    return selector(store);
  },
  selectSelectedNodeId: (s: { selectedNodeId: string | null }) => s.selectedNodeId,
  selectSelectPage: (s: { selectPage: typeof _mockSelectPage }) => s.selectPage,
  selectSetActiveSection: (s: { setActiveSection: typeof _mockSetActiveSection }) =>
    s.setActiveSection,
  selectVaultId: () => "default",
}));

// useShallow — in vitest/jsdom zustand's useShallow is not needed; identity works.
vi.mock("zustand/react/shallow", () => ({
  useShallow: (fn: unknown) => fn,
}));

// ─── Import component AFTER all mocks ────────────────────────────────────────

import * as pagesClient from "../api/pagesClient";
const mockedFetch = pagesClient.fetchPageContent as ReturnType<typeof vi.fn>;

import { NoteView } from "../components/wiki/NoteView";
import type { PageContentResponse } from "../api/types";

// ─── Fixture ─────────────────────────────────────────────────────────────────

const PAGE_CONTENT: PageContentResponse = {
  id: "page-abc",
  title: "Temperature Scaling",
  file_path: "wiki/concepts/temperature_scaling.md",
  content: "# Temperature Scaling\n\nSee [[Temperature Scaling]] here.",
  content_hash: "sha256-abc123",
  updated_at: "2025-06-30T10:00:00Z",
};

const GRAPH_NODES: GraphNode[] = [
  { id: "page-abc", title: "Temperature Scaling", type: "concept", x: 0, y: 0 },
  { id: "page-def", title: "Softmax Function", type: "concept", x: 1, y: 1 },
];

// ─── Helpers ──────────────────────────────────────────────────────────────────

/** Wait for the page to load and reach read mode. */
async function renderAndWaitReady() {
  render(<NoteView />);
  await waitFor(() => screen.getByTestId("note-edit-btn"));
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe("NoteView — wikilink click→select resolution (Task A)", () => {
  beforeEach(() => {
    _selectedNodeId = "page-abc";
    _nodes = GRAPH_NODES;
    vi.clearAllMocks();
    mockedFetch.mockResolvedValue(PAGE_CONTENT);
  });

  afterEach(() => {
    _selectedNodeId = null;
    _nodes = [];
  });

  // ── 1. Click matching wikilink → selectPage called ─────────────────────────

  it("clicking a .wikilink whose title matches a node calls selectPage with that node id", async () => {
    await renderAndWaitReady();

    const body = screen.getByTestId("note-view");
    // Find the .wikilink anchor rendered by the mocked renderMarkdown
    // It's inside the .note-view__body which is inside [data-testid="note-view"]
    const anchor = body.querySelector("a.wikilink") as HTMLElement;
    expect(anchor).toBeTruthy();
    expect(anchor.getAttribute("data-wikilink")).toBe("Temperature Scaling");

    act(() => {
      fireEvent.click(anchor);
    });

    // selectPage called with the correct node id (from GRAPH_NODES) and source "tree"
    expect(_mockSelectPage).toHaveBeenCalledWith("page-abc", "tree");
    expect(mockShowToast).not.toHaveBeenCalled();
  });

  // ── 2. Case-insensitive title match ────────────────────────────────────────

  it("resolves wikilink title case-insensitively", async () => {
    // Override renderMarkdown mock to emit lowercase title
    // We'll inject a node whose title casing differs from the anchor text
    _nodes = [{ id: "page-xyz", title: "TEMPERATURE SCALING", type: "entity", x: 0, y: 0 }];

    await renderAndWaitReady();

    const body = screen.getByTestId("note-view");
    const anchor = body.querySelector("a.wikilink") as HTMLElement;
    // data-wikilink = "Temperature Scaling" (from mock), node title = "TEMPERATURE SCALING"
    act(() => {
      fireEvent.click(anchor);
    });

    expect(_mockSelectPage).toHaveBeenCalledWith("page-xyz", "tree");
  });

  // ── 3. No matching node → toast "page not found" ───────────────────────────

  it("shows wikilinkNotFound toast when no node matches the title", async () => {
    _nodes = [
      // No node with title "Temperature Scaling"
      { id: "page-other", title: "Completely Unrelated", type: "concept", x: 0, y: 0 },
    ];

    await renderAndWaitReady();

    const body = screen.getByTestId("note-view");
    const anchor = body.querySelector("a.wikilink") as HTMLElement;

    await act(async () => {
      fireEvent.click(anchor);
      // Let the fetchPageBySlug fallback (rejected) flush before asserting.
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(_mockSelectPage).not.toHaveBeenCalled();
    expect(mockShowToast).toHaveBeenCalledWith("Page not found: Temperature Scaling", "error");
  });

  // ── 3b. Piped wikilink (slug target, no title match) → resolves via fetchPageBySlug ──

  it("falls back to fetchPageBySlug when the target is a slug, not a title", async () => {
    // No node titled "temperature-scaling" — only the slugified-title mismatch case
    // a piped [[slug|Display]] wikilink produces (data-wikilink carries the slug).
    _nodes = [{ id: "page-other", title: "Completely Unrelated", type: "concept", x: 0, y: 0 }];
    const mockedFetchBySlug = pagesClient.fetchPageBySlug as ReturnType<typeof vi.fn>;
    mockedFetchBySlug.mockResolvedValueOnce({
      id: "page-slug-target",
      title: "Temperature Scaling",
    });

    await renderAndWaitReady();

    const body = screen.getByTestId("note-view");
    const anchor = body.querySelector("a.wikilink") as HTMLElement;
    anchor.setAttribute("data-wikilink", "temperature-scaling");

    await act(async () => {
      fireEvent.click(anchor);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mockedFetchBySlug).toHaveBeenCalledWith("temperature-scaling");
    expect(_mockSelectPage).toHaveBeenCalledWith("page-slug-target", "tree");
    expect(mockShowToast).not.toHaveBeenCalled();
  });

  // ── 4. Click on non-wikilink element → no selectPage ──────────────────────

  it("clicking on plain text (not a .wikilink) does NOT call selectPage", async () => {
    await renderAndWaitReady();

    const body = screen.getByTestId("note-view");
    // Click the body container itself (not the anchor)
    act(() => {
      fireEvent.click(body);
    });

    expect(_mockSelectPage).not.toHaveBeenCalled();
  });
});

// ─── Task C — type badge tests ────────────────────────────────────────────────

describe("NoteView — type badge (Task C)", () => {
  beforeEach(() => {
    _selectedNodeId = "page-abc";
    vi.clearAllMocks();
    mockedFetch.mockResolvedValue(PAGE_CONTENT);
  });

  afterEach(() => {
    _selectedNodeId = null;
    _nodes = [];
  });

  it("renders note-type-badge with the node's type text when node has a type", async () => {
    _nodes = [{ id: "page-abc", title: "Temperature Scaling", type: "concept", x: 0, y: 0 }];

    await renderAndWaitReady();

    const badge = screen.getByTestId("note-type-badge");
    expect(badge).toBeTruthy();
    expect(badge.textContent).toBe("concept");
  });

  it("does NOT render note-meta-row when selected node has type = null", async () => {
    _nodes = [{ id: "page-abc", title: "Temperature Scaling", type: null, x: 0, y: 0 }];

    await renderAndWaitReady();

    expect(screen.queryByTestId("note-meta-row")).toBeNull();
    expect(screen.queryByTestId("note-type-badge")).toBeNull();
  });

  it("does NOT render note-meta-row when selected node is not in the graph nodes list", async () => {
    _nodes = []; // empty — selectedNodeId won't match anything

    await renderAndWaitReady();

    expect(screen.queryByTestId("note-meta-row")).toBeNull();
  });
});
