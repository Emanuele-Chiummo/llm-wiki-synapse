/**
 * NoteView.test.tsx — vitest + React Testing Library tests for the wiki note viewer/editor.
 *
 * Coverage:
 *   1. No selection → EmptyState rendered
 *   2. Selection set → fetches content; title appears in read mode
 *   3. Edit button → switches to edit mode (mock editor container present)
 *   4. Cancel button → switches back to read mode without saving
 *   5. Save → calls savePageContent with correct args; returns to read mode + success toast
 *   6. Save 409 → stale-conflict toast + Reload button; Reload re-fetches
 *   7. Fetch error → error state rendered with Retry affordance
 *   8. I3 gate: renderMarkdown is NOT called while in edit mode (no per-keystroke parse)
 *
 * CodeMirror isolation: CodeMirrorEditor is mocked at the module path boundary so
 * the real @codemirror/* bundle (~4 MB) is never loaded into the jsdom worker.
 *
 * Uses fireEvent (not userEvent) to avoid the @testing-library/user-event memory
 * overhead that causes OOM crashes in this project's Node 26 + vitest 2.1 + jsdom
 * worker configuration.
 *
 * All network calls mocked via vi.mock.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act, fireEvent } from "@testing-library/react";

// ─── Mock CodeMirrorEditor — must come BEFORE the component import ────────────
// Prevents real @codemirror/* imports from loading in the jsdom worker.

vi.mock("../components/wiki/CodeMirrorEditor", () => ({
  CodeMirrorEditor: ({
    initialContent,
    handleRef,
  }: {
    initialContent: string;
    handleRef: { current: { getContent: () => string } | null };
  }) => {
    // Synchronously wire the handle so Save can call getContent().
    // This runs during render rather than in a useEffect, which is safe
    // for testing purposes (we just need the ref populated before the
    // save button click is processed).
    handleRef.current = { getContent: () => initialContent };
    return null;
  },
}));

// ─── Mock API client ──────────────────────────────────────────────────────────

vi.mock("../api/pagesClient", () => ({
  fetchPageContent: vi.fn(),
  savePageContent: vi.fn(),
  fetchPages: vi.fn(),
  fetchAllPages: vi.fn().mockResolvedValue({ items: [] }),
  fetchStatus: vi.fn(),
  // fetchRelatedPages must be present; return empty response so related panel
  // stays hidden and does not interfere with existing test assertions.
  fetchRelatedPages: vi.fn().mockResolvedValue({ items: [], total: 0 }),
}));

import * as pagesClient from "../api/pagesClient";
const mockedFetch = pagesClient.fetchPageContent as ReturnType<typeof vi.fn>;
const mockedSave = pagesClient.savePageContent as ReturnType<typeof vi.fn>;

// ─── Mock Toast ───────────────────────────────────────────────────────────────

const mockShowToast = vi.fn();
vi.mock("../components/common/Toast", () => ({
  showToast: (...args: unknown[]) => mockShowToast(...args),
  ToastHost: () => null,
}));

// ─── Mock i18n ────────────────────────────────────────────────────────────────
// IMPORTANT: `t` must be a STABLE function reference across renders.
// NoteView uses t as a dep of useCallback(loadPage,[t]) which feeds useEffect.
// A new `t` object on every useTranslation() call causes infinite re-render loop.
// We solve this by creating one singleton result object inside the factory closure.

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
    "noteView.sources": "Sources",
    "noteView.related": "Related ({{count}})",
    "noteView.relatedError": "Could not load related pages",
    "noteView.tagsMore": "More (+{{count}})",
    "noteView.tagsCollapse": "Fewer",
    "noteView.updatedLabel": "updated: {{iso}}",
    "noteView.metaExpand": "Show metadata",
    "noteView.metaCollapse": "Hide metadata",
    "common.loading": "Loading…",
    "common.retry": "Retry",
  };
  // Singleton — same object reference returned on every useTranslation() call.
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

// ─── Mock renderMarkdown (track call count for I3 assertion) ──────────────────

const mockRenderMarkdown = vi.fn((raw: string) => `<p>${raw}</p>`);
vi.mock("../components/chat/renderMarkdown", () => ({
  renderMarkdown: (raw: string) => mockRenderMarkdown(raw),
  stripLeadingFrontmatter: (raw: string) => raw,
}));

// ─── Mock ApiError — defined inline to avoid hoist-before-init ───────────────

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

// ─── Mock graphStore ──────────────────────────────────────────────────────────
// NoteView now also reads nodes (for wikilink resolution + type badge) and
// selectPage action. These tests don't exercise those features, so we return
// stable empty defaults.

let _selectedNodeId: string | null = null;
const _mockSelectPage = vi.fn();

const _mockSetActiveSection = vi.fn();

vi.mock("../store/graphStore", () => ({
  useGraphStore: (selector: (s: {
    selectedNodeId: string | null;
    nodes: [];
    edges: [];
    selectPage: typeof _mockSelectPage;
    setActiveSection: typeof _mockSetActiveSection;
  }) => unknown) =>
    selector({ selectedNodeId: _selectedNodeId, nodes: [], edges: [], selectPage: _mockSelectPage, setActiveSection: _mockSetActiveSection }),
  selectSelectedNodeId: (s: { selectedNodeId: string | null }) => s.selectedNodeId,
  selectNodes: (s: { nodes: [] }) => s.nodes,
  selectEdges: (s: { edges: [] }) => s.edges,
  selectSelectPage: (s: { selectPage: typeof _mockSelectPage }) => s.selectPage,
  selectSetActiveSection: (s: { setActiveSection: typeof _mockSetActiveSection }) => s.setActiveSection,
  selectVaultId: () => "default",
}));

// useShallow is called by NoteView to wrap the selectNodes selector.
// In the jsdom test environment we don't need the real shallow comparison;
// returning the selector function directly is equivalent.
vi.mock("zustand/react/shallow", () => ({
  useShallow: (fn: unknown) => fn,
}));

// ─── Import component and ApiError AFTER all mocks ────────────────────────────

import { NoteView } from "../components/wiki/NoteView";
import { ApiError as MockApiError } from "../api/graphClient";
import type { PageContentResponse, PageContentPutResponse } from "../api/types";

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const PAGE_CONTENT: PageContentResponse = {
  id: "page-abc",
  title: "Temperature Scaling",
  file_path: "wiki/concepts/temperature_scaling.md",
  content: "# Temperature Scaling\n\nThis is the content.",
  content_hash: "sha256-abc123",
  updated_at: "2025-06-30T10:00:00Z",
};

const SAVE_RESPONSE: PageContentPutResponse = {
  id: "page-abc",
  content_hash: "sha256-new456",
  updated_at: "2025-06-30T11:00:00Z",
};

// ─── Tests ────────────────────────────────────────────────────────────────────

describe("NoteView", () => {
  beforeEach(() => {
    _selectedNodeId = null;
    vi.clearAllMocks();
  });

  afterEach(() => {
    _selectedNodeId = null;
  });

  // ── 1. No selection ─────────────────────────────────────────────────────────

  it("renders EmptyState when no page is selected", () => {
    _selectedNodeId = null;
    render(<NoteView />);

    expect(screen.getByTestId("note-view")).toBeDefined();
    expect(screen.getByTestId("note-view-empty")).toBeDefined();
    expect(screen.getByText("Select a page to read or edit")).toBeDefined();
    expect(mockedFetch).not.toHaveBeenCalled();
  });

  // ── 2. Selection set → read mode ────────────────────────────────────────────

  it("fetches content when a page is selected and renders the title in read mode", async () => {
    _selectedNodeId = "page-abc";
    mockedFetch.mockResolvedValue(PAGE_CONTENT);

    render(<NoteView />);

    await waitFor(() => {
      expect(screen.getByText("Temperature Scaling")).toBeDefined();
    });

    expect(mockedFetch).toHaveBeenCalledWith("page-abc", expect.any(AbortSignal));
    expect(screen.getByTestId("note-edit-btn")).toBeDefined();
    // Editor should NOT be present in read mode
    expect(screen.queryByTestId("codemirror-editor")).toBeNull();
    // renderMarkdown called exactly once entering read mode (I3)
    expect(mockRenderMarkdown).toHaveBeenCalledTimes(1);
  });

  // ── 3. Click Edit → edit mode ───────────────────────────────────────────────

  it("switches to edit mode when Edit is clicked", async () => {
    _selectedNodeId = "page-abc";
    mockedFetch.mockResolvedValue(PAGE_CONTENT);

    render(<NoteView />);
    await waitFor(() => screen.getByTestId("note-edit-btn"));

    const callsBefore = mockRenderMarkdown.mock.calls.length;

    act(() => {
      fireEvent.click(screen.getByTestId("note-edit-btn"));
    });

    // Edit mode active (Save + Cancel visible)
    expect(screen.getByTestId("note-save-btn")).toBeDefined();
    expect(screen.getByTestId("note-cancel-btn")).toBeDefined();
    // Edit button gone while in edit mode
    expect(screen.queryByTestId("note-edit-btn")).toBeNull();

    // renderMarkdown NOT called again after entering edit mode (I3)
    expect(mockRenderMarkdown.mock.calls.length).toBe(callsBefore);
  });

  // ── 4. Cancel → back to read mode ──────────────────────────────────────────

  it("returns to read mode when Cancel is clicked without saving", async () => {
    _selectedNodeId = "page-abc";
    mockedFetch.mockResolvedValue(PAGE_CONTENT);

    render(<NoteView />);
    await waitFor(() => screen.getByTestId("note-edit-btn"));

    act(() => { fireEvent.click(screen.getByTestId("note-edit-btn")); });
    act(() => { fireEvent.click(screen.getByTestId("note-cancel-btn")); });

    // Back to read mode
    expect(screen.getByTestId("note-edit-btn")).toBeDefined();
    expect(screen.queryByTestId("note-save-btn")).toBeNull();
    // savePageContent never called
    expect(mockedSave).not.toHaveBeenCalled();
  });

  // ── 5. Save 200 → read mode + success toast ─────────────────────────────────

  it("calls savePageContent with correct args and returns to read mode on 200", async () => {
    _selectedNodeId = "page-abc";
    mockedFetch.mockResolvedValue(PAGE_CONTENT);
    mockedSave.mockResolvedValue(SAVE_RESPONSE);

    render(<NoteView />);
    await waitFor(() => screen.getByTestId("note-edit-btn"));

    act(() => { fireEvent.click(screen.getByTestId("note-edit-btn")); });

    await act(async () => {
      fireEvent.click(screen.getByTestId("note-save-btn"));
    });

    await waitFor(() => {
      // savePageContent called with: pageId, content (mock editor returns initialContent), hash
      expect(mockedSave).toHaveBeenCalledWith(
        "page-abc",
        PAGE_CONTENT.content,
        PAGE_CONTENT.content_hash,
      );
    });

    // Returns to read mode
    await waitFor(() => {
      expect(screen.getByTestId("note-edit-btn")).toBeDefined();
      expect(screen.queryByTestId("note-save-btn")).toBeNull();
    });

    // Success toast fired
    expect(mockShowToast).toHaveBeenCalledWith("Saved", "success");
  });

  // ── 6. Save 409 → stale-conflict affordance + Reload ────────────────────────

  it("shows stale-conflict toast and Reload button on 409, then re-fetches on Reload", async () => {
    _selectedNodeId = "page-abc";
    mockedFetch.mockResolvedValue(PAGE_CONTENT);
    mockedSave.mockRejectedValue(new MockApiError(409, "Hash mismatch"));

    render(<NoteView />);
    await waitFor(() => screen.getByTestId("note-edit-btn"));

    act(() => { fireEvent.click(screen.getByTestId("note-edit-btn")); });

    await act(async () => {
      fireEvent.click(screen.getByTestId("note-save-btn"));
    });

    await waitFor(() => {
      // Error toast with stale-conflict message
      expect(mockShowToast).toHaveBeenCalledWith(
        "This note changed on disk — reload before editing",
        "error",
      );
      // Reload button appears
      expect(screen.getByTestId("note-reload-btn")).toBeDefined();
    });

    // Clicking Reload re-fetches the page
    const freshContent = { ...PAGE_CONTENT, content_hash: "sha256-new456" };
    mockedFetch.mockResolvedValue(freshContent);

    await act(async () => {
      fireEvent.click(screen.getByTestId("note-reload-btn"));
    });

    await waitFor(() => {
      expect(mockedFetch).toHaveBeenCalledTimes(2);
      // Stale Reload button gone after reload
      expect(screen.queryByTestId("note-reload-btn")).toBeNull();
    });
  });

  // ── 7. Fetch error → error state ───────────────────────────────────────────

  it("renders error state when fetchPageContent rejects", async () => {
    _selectedNodeId = "page-abc";
    mockedFetch.mockRejectedValue(new Error("Network error"));

    render(<NoteView />);

    await waitFor(() => {
      expect(screen.getByTestId("note-view-error")).toBeDefined();
    });

    // No edit affordance in error state
    expect(screen.queryByTestId("note-edit-btn")).toBeNull();
  });

  // ── 8. I3: renderMarkdown not called while in edit mode ────────────────────

  it("calls renderMarkdown exactly once on load and not again when entering edit mode", async () => {
    _selectedNodeId = "page-abc";
    mockedFetch.mockResolvedValue(PAGE_CONTENT);

    render(<NoteView />);
    await waitFor(() => screen.getByTestId("note-edit-btn"));
    // renderMarkdown called once on initial read-mode render
    expect(mockRenderMarkdown).toHaveBeenCalledTimes(1);

    act(() => { fireEvent.click(screen.getByTestId("note-edit-btn")); });
    // Still exactly 1 call after entering edit mode — no re-parse on mode switch (I3)
    expect(mockRenderMarkdown).toHaveBeenCalledTimes(1);
  });

  // ── R2: ISO updated line ───────────────────────────────────────────────────
  // WS-D7: ISO line lives in the collapsible Tier 2 — must expand meta first.

  it("R2: renders the ISO updated line when updated_at is present (after expanding meta)", async () => {
    _selectedNodeId = "page-abc";
    mockedFetch.mockResolvedValue(PAGE_CONTENT); // updated_at: "2025-06-30T10:00:00Z"

    render(<NoteView />);
    await waitFor(() => screen.getByTestId("note-edit-btn"));

    // Metadata is always visible (WS-D7: header scrolls with the body, no collapse).
    await waitFor(() => screen.getByTestId("note-updated-iso"));

    // The label should contain the ISO string
    expect(screen.getByTestId("note-updated-iso").textContent).toContain("2025-06-30T10:00:00Z");
  });

  it("R2: does not render the ISO updated line when updated_at is absent", async () => {
    _selectedNodeId = "page-abc";
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    const { updated_at: _omit, ...noUpdated } = PAGE_CONTENT;
    mockedFetch.mockResolvedValue({ ...noUpdated } as PageContentResponse);

    render(<NoteView />);
    await waitFor(() => screen.getByTestId("note-edit-btn"));

    // Metadata is always visible, but updated_at is absent so the ISO line must not render.
    await waitFor(() => screen.getByTestId("note-meta-expanded"));
    expect(screen.queryByTestId("note-updated-iso")).toBeNull();
  });
});

// ─── R1: Tag overflow ─────────────────────────────────────────────────────────
// WS-D7: tags live in the collapsible Tier 2 — helpers expand meta before testing.

/** Metadata is always visible (WS-D7: header scrolls with the body, no collapse). */
async function expandMeta() {
  await waitFor(() => screen.getByTestId("note-meta-expanded"));
}

describe("NoteView — R1 tag overflow", () => {
  beforeEach(() => {
    _selectedNodeId = "page-abc";
  });

  afterEach(() => {
    _selectedNodeId = null;
  });

  it("renders all chips when tag count is within MAX_VISIBLE_TAGS (24)", async () => {
    const tags = Array.from({ length: 5 }, (_, i) => `tag${i}`);
    mockedFetch.mockResolvedValue({ ...PAGE_CONTENT, tags });

    render(<NoteView />);
    await waitFor(() => screen.getByTestId("note-edit-btn"));
    await expandMeta();

    await waitFor(() => {
      const chips = screen.getAllByTestId("note-tag-chip");
      expect(chips.length).toBe(5);
    });
    expect(screen.queryByTestId("note-tags-more")).toBeNull();
  });

  it("renders only 24 chips and a 'More' button when tags exceed MAX", async () => {
    const tags = Array.from({ length: 30 }, (_, i) => `tag${i}`);
    mockedFetch.mockResolvedValue({ ...PAGE_CONTENT, tags });

    render(<NoteView />);
    await waitFor(() => screen.getByTestId("note-edit-btn"));
    await expandMeta();

    await waitFor(() => {
      const chips = screen.getAllByTestId("note-tag-chip");
      // Only first 24 visible while TagOverflow is collapsed
      expect(chips.length).toBe(24);
    });
    expect(screen.getByTestId("note-tags-more")).toBeTruthy();
    // "More (+6)" text
    expect(screen.getByTestId("note-tags-more").textContent).toContain("6");
  });

  it("expands to show all chips when 'More' is clicked", async () => {
    const tags = Array.from({ length: 30 }, (_, i) => `tag${i}`);
    mockedFetch.mockResolvedValue({ ...PAGE_CONTENT, tags });

    render(<NoteView />);
    await waitFor(() => screen.getByTestId("note-edit-btn"));
    await expandMeta();
    await waitFor(() => screen.getByTestId("note-tags-more"));

    act(() => { fireEvent.click(screen.getByTestId("note-tags-more")); });

    await waitFor(() => {
      const chips = screen.getAllByTestId("note-tag-chip");
      expect(chips.length).toBe(30);
    });
    // "More" gone, "Fewer" visible
    expect(screen.queryByTestId("note-tags-more")).toBeNull();
    expect(screen.getByTestId("note-tags-collapse")).toBeTruthy();
  });

  it("collapses back when 'Fewer' is clicked", async () => {
    const tags = Array.from({ length: 30 }, (_, i) => `tag${i}`);
    mockedFetch.mockResolvedValue({ ...PAGE_CONTENT, tags });

    render(<NoteView />);
    await waitFor(() => screen.getByTestId("note-edit-btn"));
    await expandMeta();
    await waitFor(() => screen.getByTestId("note-tags-more"));

    // Expand TagOverflow
    act(() => { fireEvent.click(screen.getByTestId("note-tags-more")); });
    await waitFor(() => screen.getByTestId("note-tags-collapse"));

    // Collapse TagOverflow
    act(() => { fireEvent.click(screen.getByTestId("note-tags-collapse")); });
    await waitFor(() => {
      const chips = screen.getAllByTestId("note-tag-chip");
      expect(chips.length).toBe(24);
    });
    expect(screen.getByTestId("note-tags-more")).toBeTruthy();
  });

  // ── WS-D7: metadata scrolls with the body (not sticky, not collapsible) ───────

  it("WS-D7: metadata section is always visible with no collapse toggle", async () => {
    mockedFetch.mockResolvedValue(PAGE_CONTENT);

    render(<NoteView />);
    await waitFor(() => screen.getByTestId("note-edit-btn"));

    // Metadata (tags/sources/related) is always rendered — it scrolls with the body.
    await waitFor(() => screen.getByTestId("note-meta-expanded"));
    // The collapse toggle no longer exists.
    expect(screen.queryByTestId("note-meta-toggle")).toBeNull();
  });
});
