/**
 * NoteViewDirty.test.tsx — R7-4: Unsaved-changes indicator + navigation guard.
 *
 * Coverage:
 *   1. isDirty false on initial load → no dot, no hint
 *   2. isDirty true after onContentChange fires with different content → dot + hint visible
 *   3. isDirty false after onContentChange returns to original content
 *   4. Cancel while dirty → ConfirmDialog shown (not immediately cancelled)
 *   5. ConfirmDialog confirm → mode returns to read
 *   6. ConfirmDialog cancel → stays in edit mode
 *   7. Cancel while NOT dirty → no dialog, immediate return to read
 *   8. Save success → isDirty cleared, back to read
 *
 * Mocking strategy follows NoteView.test.tsx patterns:
 *   - vi.mock for CodeMirrorEditor (avoid loading real 4MB CM bundle)
 *   - onContentChange captured from props so tests can trigger dirty state
 *   - i18n singleton pattern (same as NoteView.test.tsx)
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act, fireEvent } from "@testing-library/react";

// ─── Mock CodeMirrorEditor ────────────────────────────────────────────────────
// Capture `onContentChange` so tests can trigger it, and wire handleRef
// with the initial content returned from getContent().

let capturedOnContentChange: ((content: string) => void) | undefined;
let capturedInitialContent: string = "";

vi.mock("../components/wiki/CodeMirrorEditor", () => ({
  CodeMirrorEditor: ({
    initialContent,
    handleRef,
    onContentChange,
  }: {
    initialContent: string;
    handleRef: { current: { getContent: () => string } | null };
    onContentChange?: (content: string) => void;
  }) => {
    capturedInitialContent = initialContent;
    capturedOnContentChange = onContentChange;
    // Wire the handle so Save can call getContent().
    handleRef.current = { getContent: () => capturedInitialContent };
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

vi.mock("react-i18next", () => {
  const map: Record<string, string> = {
    "noteView.selectPagePrompt": "Select a page",
    "noteView.selectPageBody": "Click a page.",
    "noteView.edit": "Edit",
    "noteView.save": "Save",
    "noteView.cancel": "Cancel",
    "noteView.saved": "Saved",
    "noteView.saving": "Saving…",
    "noteView.staleConflict": "Stale conflict",
    "noteView.reload": "Reload",
    "noteView.loadError": "Load error",
    "noteView.sources": "Sources",
    "noteView.related": "Related ({{count}})",
    "noteView.relatedError": "Could not load related pages",
    "noteView.wikilinkNotFound": "Not found: {{title}}",
    "noteView.unsavedDot": "Unsaved changes",
    "noteView.unsavedHint": "unsaved changes",
    "noteView.unsavedDialogTitle": "Unsaved changes",
    "noteView.unsavedDialogBody": "Discard and leave?",
    "noteView.unsavedDiscard": "Discard",
    "noteView.unsavedKeepEditing": "Keep editing",
    "common.loading": "Loading…",
    "common.retry": "Retry",
    "common.close": "Close",
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

// ─── Mock renderMarkdown ──────────────────────────────────────────────────────

vi.mock("../components/chat/renderMarkdown", () => ({
  renderMarkdown: (raw: string) => `<p>${raw}</p>`,
  stripLeadingFrontmatter: (raw: string) => raw,
}));

// ─── Mock ApiError ────────────────────────────────────────────────────────────

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

let _selectedNodeId: string | null = null;
const _mockSelectPage = vi.fn();
const _mockSetActiveSection = vi.fn();

vi.mock("../store/graphStore", () => ({
  useGraphStore: (
    selector: (s: {
      selectedNodeId: string | null;
      nodes: [];
      selectPage: typeof _mockSelectPage;
      setActiveSection: typeof _mockSetActiveSection;
    }) => unknown,
  ) =>
    selector({
      selectedNodeId: _selectedNodeId,
      nodes: [],
      selectPage: _mockSelectPage,
      setActiveSection: _mockSetActiveSection,
    }),
  selectSelectedNodeId: (s: { selectedNodeId: string | null }) => s.selectedNodeId,
  selectNodes: (s: { nodes: [] }) => s.nodes,
  selectSelectPage: (s: { selectPage: typeof _mockSelectPage }) => s.selectPage,
  selectSetActiveSection: (s: { setActiveSection: typeof _mockSetActiveSection }) =>
    s.setActiveSection,
  selectVaultId: () => "default",
}));

vi.mock("zustand/react/shallow", () => ({
  useShallow: (fn: unknown) => fn,
}));

// ─── Imports after mocks ──────────────────────────────────────────────────────

import { NoteView } from "../components/wiki/NoteView";
import type { PageContentResponse, PageContentPutResponse } from "../api/types";

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const ORIGINAL_CONTENT = "# My Page\n\nOriginal content.";

const PAGE_CONTENT: PageContentResponse = {
  id: "page-dirty-1",
  title: "My Page",
  file_path: "wiki/concepts/my_page.md",
  content: ORIGINAL_CONTENT,
  content_hash: "sha256-original",
  updated_at: "2025-07-01T10:00:00Z",
};

const SAVE_RESPONSE: PageContentPutResponse = {
  id: "page-dirty-1",
  content_hash: "sha256-new",
  updated_at: "2025-07-01T11:00:00Z",
};

// ─── Tests ────────────────────────────────────────────────────────────────────

describe("NoteView — R7-4 dirty state indicator", () => {
  beforeEach(() => {
    _selectedNodeId = "page-dirty-1";
    capturedOnContentChange = undefined;
    capturedInitialContent = ORIGINAL_CONTENT;
    mockedFetch.mockResolvedValue(PAGE_CONTENT);
    mockedSave.mockResolvedValue(SAVE_RESPONSE);
    _mockSelectPage.mockReset();
    _mockSetActiveSection.mockReset();
    mockShowToast.mockReset();
  });

  afterEach(() => {
    _selectedNodeId = null;
  });

  // ── 1. No dirty indicator on load ──────────────────────────────────────────

  it("shows no dirty dot or hint in read mode", async () => {
    render(<NoteView />);
    await waitFor(() => screen.getByTestId("note-edit-btn"));

    expect(screen.queryByTestId("note-unsaved-dot")).toBeNull();
    expect(screen.queryByTestId("note-unsaved-hint")).toBeNull();
  });

  it("shows no dirty dot or hint after entering edit mode (before any change)", async () => {
    render(<NoteView />);
    await waitFor(() => screen.getByTestId("note-edit-btn"));

    act(() => {
      fireEvent.click(screen.getByTestId("note-edit-btn"));
    });

    expect(screen.queryByTestId("note-unsaved-dot")).toBeNull();
    expect(screen.queryByTestId("note-unsaved-hint")).toBeNull();
  });

  // ── 2. Dirty dot + hint after content change ────────────────────────────────

  it("shows dirty dot and hint when onContentChange fires with different content", async () => {
    render(<NoteView />);
    await waitFor(() => screen.getByTestId("note-edit-btn"));

    act(() => {
      fireEvent.click(screen.getByTestId("note-edit-btn"));
    });

    // Simulate a user edit
    act(() => {
      capturedOnContentChange?.("# My Page\n\nModified content.");
    });

    expect(screen.getByTestId("note-unsaved-dot")).toBeDefined();
    expect(screen.getByTestId("note-unsaved-hint")).toBeDefined();
    expect(screen.getByTestId("note-unsaved-hint").textContent).toContain("unsaved changes");
  });

  // ── 3. Dirty cleared when content reverts to original ──────────────────────

  it("clears dirty dot when content reverts to original", async () => {
    render(<NoteView />);
    await waitFor(() => screen.getByTestId("note-edit-btn"));

    act(() => {
      fireEvent.click(screen.getByTestId("note-edit-btn"));
    });

    act(() => {
      capturedOnContentChange?.("changed");
    });
    expect(screen.getByTestId("note-unsaved-dot")).toBeDefined();

    act(() => {
      capturedOnContentChange?.(ORIGINAL_CONTENT);
    });
    expect(screen.queryByTestId("note-unsaved-dot")).toBeNull();
  });

  // ── 4. Cancel while dirty → ConfirmDialog shown ────────────────────────────

  it("shows ConfirmDialog when Cancel is clicked while dirty", async () => {
    render(<NoteView />);
    await waitFor(() => screen.getByTestId("note-edit-btn"));

    act(() => {
      fireEvent.click(screen.getByTestId("note-edit-btn"));
    });
    act(() => {
      capturedOnContentChange?.("changed content");
    });

    act(() => {
      fireEvent.click(screen.getByTestId("note-cancel-btn"));
    });

    expect(screen.getByTestId("confirm-dialog")).toBeDefined();
    // Still in edit mode (not cancelled yet)
    expect(screen.getByTestId("note-cancel-btn")).toBeDefined();
  });

  // ── 5. ConfirmDialog confirm → returns to read mode ──────────────────────────

  it("returns to read mode when ConfirmDialog confirm is clicked", async () => {
    render(<NoteView />);
    await waitFor(() => screen.getByTestId("note-edit-btn"));

    act(() => {
      fireEvent.click(screen.getByTestId("note-edit-btn"));
    });
    act(() => {
      capturedOnContentChange?.("changed content");
    });
    act(() => {
      fireEvent.click(screen.getByTestId("note-cancel-btn"));
    });

    // Click confirm in the dialog
    act(() => {
      fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));
    });

    // Back to read mode
    await waitFor(() => {
      expect(screen.getByTestId("note-edit-btn")).toBeDefined();
      expect(screen.queryByTestId("note-cancel-btn")).toBeNull();
    });
  });

  // ── 6. ConfirmDialog cancel → stays in edit mode ──────────────────────────────

  it("stays in edit mode when ConfirmDialog cancel is clicked", async () => {
    render(<NoteView />);
    await waitFor(() => screen.getByTestId("note-edit-btn"));

    act(() => {
      fireEvent.click(screen.getByTestId("note-edit-btn"));
    });
    act(() => {
      capturedOnContentChange?.("changed content");
    });
    act(() => {
      fireEvent.click(screen.getByTestId("note-cancel-btn"));
    });

    // Click cancel in the dialog
    act(() => {
      fireEvent.click(screen.getByTestId("confirm-dialog-cancel"));
    });

    // Still in edit mode
    expect(screen.getByTestId("note-cancel-btn")).toBeDefined();
    expect(screen.queryByTestId("confirm-dialog")).toBeNull();
  });

  // ── 7. Cancel while NOT dirty → no dialog ─────────────────────────────────

  it("cancels immediately without dialog when not dirty", async () => {
    render(<NoteView />);
    await waitFor(() => screen.getByTestId("note-edit-btn"));

    act(() => {
      fireEvent.click(screen.getByTestId("note-edit-btn"));
    });
    // No content change — not dirty
    act(() => {
      fireEvent.click(screen.getByTestId("note-cancel-btn"));
    });

    // Back to read mode immediately, no dialog
    expect(screen.queryByTestId("confirm-dialog")).toBeNull();
    expect(screen.getByTestId("note-edit-btn")).toBeDefined();
  });

  // ── 8. Save success → dirty cleared ────────────────────────────────────────

  it("clears dirty state and returns to read mode on successful save", async () => {
    render(<NoteView />);
    await waitFor(() => screen.getByTestId("note-edit-btn"));

    act(() => {
      fireEvent.click(screen.getByTestId("note-edit-btn"));
    });
    act(() => {
      capturedOnContentChange?.("new content");
    });

    expect(screen.getByTestId("note-unsaved-dot")).toBeDefined();

    await act(async () => {
      fireEvent.click(screen.getByTestId("note-save-btn"));
    });

    await waitFor(() => {
      expect(screen.getByTestId("note-edit-btn")).toBeDefined();
      expect(screen.queryByTestId("note-unsaved-dot")).toBeNull();
    });
  });

  // ── 9. F1: "Keep editing" must not re-open the dialog ─────────────────────
  //
  // Repro of the infinite loop (pre-fix):
  //   1. Editing page A with isDirty=true.
  //   2. User clicks tree node B → guard effect fires → dialog shown.
  //   3. User clicks "Keep editing" → handleNavCancel calls selectPage(A) to restore selection.
  //   4. selectedNodeId changes back to A → guard re-fires with prev=B, current=A → dialog opens again.
  //
  // Fix: suppressGuardRef.current is set to true before selectPage(); the guard effect returns
  // early on the next run and does not open the dialog.

  it("clicking Keep editing does not re-open the dialog when selection is restored", async () => {
    _selectedNodeId = "page-dirty-1";
    mockedFetch.mockResolvedValue(PAGE_CONTENT);
    _mockSelectPage.mockReset();

    const { rerender } = render(<NoteView />);
    await waitFor(() => screen.getByTestId("note-edit-btn"));

    // Enter edit mode.
    act(() => {
      fireEvent.click(screen.getByTestId("note-edit-btn"));
    });

    // Make the buffer dirty.
    act(() => {
      capturedOnContentChange?.("modified content — different from original");
    });

    // Simulate the user clicking a different tree node (selectedNodeId changes to page-dirty-2).
    _selectedNodeId = "page-dirty-2";
    act(() => {
      rerender(<NoteView />);
    });

    // Guard fires → dialog must appear.
    await waitFor(() => {
      expect(screen.getByTestId("confirm-dialog")).toBeDefined();
    });

    // Click "Keep editing" (the cancel button in the dialog).
    act(() => {
      fireEvent.click(screen.getByTestId("confirm-dialog-cancel"));
    });

    // selectPage should have been called to restore the original selection.
    expect(_mockSelectPage).toHaveBeenCalledWith("page-dirty-1", "tree");

    // Dialog must close immediately.
    expect(screen.queryByTestId("confirm-dialog")).toBeNull();

    // Simulate the tree/store restoring selectedNodeId to page-dirty-1 in response to selectPage().
    // Without the suppressGuardRef fix this would re-trigger the guard and reopen the dialog.
    _selectedNodeId = "page-dirty-1";
    act(() => {
      rerender(<NoteView />);
    });

    // Flush any pending microtasks / state updates.
    await act(async () => {
      await Promise.resolve();
    });

    // Dialog must NOT reopen — suppressGuardRef suppressed the guard for the programmatic restore.
    expect(screen.queryByTestId("confirm-dialog")).toBeNull();

    // User is still in edit mode (they chose "Keep editing").
    expect(screen.getByTestId("note-cancel-btn")).toBeDefined();
  });
});
