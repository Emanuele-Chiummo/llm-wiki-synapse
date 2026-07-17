/**
 * CascadeDeleteModal.test.tsx — vitest + React Testing Library tests for F13.
 *
 * Covers:
 *   - Preview loading state rendered while fetch is in-flight
 *   - Preview error state rendered when preview fetch fails
 *   - will_delete section rendered (and "only this page" message when empty)
 *   - will_preserve_with_pruned_source section rendered when present
 *   - wikilinks_to_rewrite section: count message + file list + "no wikilinks" message
 *   - shared_entity_warnings shown PROMINENTLY on step 1 (AC-F13-6a)
 *   - index_entry_will_be_removed indicator
 *   - raw_source_to_delete shown when present
 *   - No warnings → no warning banner
 *   - Cancel button: onCancel fires, no DELETE call made (AC-F13-6d)
 *   - Next button disabled while loading
 *   - Step 2 (confirm): shared-entity warnings repeated (AC-F13-6a)
 *   - Step 2: confirm button → calls cascadeDelete → onDeleted fired
 *   - Step 2: DELETE error shown inline; onDeleted NOT called
 *   - Back button on step 2 returns to step 1
 *   - ESC key fires onCancel
 *   - Clicking overlay fires onCancel
 *
 * INVARIANT I3: no heavy work per render; preview is a single fetch.
 * All network calls are mocked.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { CascadeDeleteModal } from "../components/wiki/CascadeDeleteModal";
import type { CascadePreviewResponse, CascadeDeleteResult } from "../api/types";

// ─── Mock API client ──────────────────────────────────────────────────────────

vi.mock("../api/cascadeDeleteClient", () => ({
  previewCascadeDelete: vi.fn(),
  cascadeDelete: vi.fn(),
}));

import * as cascadeDeleteClient from "../api/cascadeDeleteClient";

// ─── Mock i18n ────────────────────────────────────────────────────────────────

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) => {
      const map: Record<string, string> = {
        "cascadeDelete.deleteButton": "Delete page",
        "cascadeDelete.modalTitle": "Delete page",
        "cascadeDelete.step1Title": "Review deletion plan",
        "cascadeDelete.step2Title": "Confirm deletion",
        "cascadeDelete.warningsBanner": "Shared-entity warnings",
        "cascadeDelete.warningsHint":
          "These pages share source overlap with the page being deleted.",
        "cascadeDelete.willDelete": "Pages that will be deleted",
        "cascadeDelete.willPreserve": "Pages preserved (source pruned)",
        "cascadeDelete.wikilinksToRewrite": "Wikilinks to rewrite",
        "cascadeDelete.wikilinksCount": `${String(opts?.count ?? 0)} dead [[wikilinks]] will be neutralised`,
        "cascadeDelete.indexEntryRemoved": "Entry in index.md will be removed",
        "cascadeDelete.rawSourceDeleted": "Raw source file will be deleted",
        "cascadeDelete.confirmButton": "Yes, delete permanently",
        "cascadeDelete.cancelButton": "Cancel",
        "cascadeDelete.backButton": "Back",
        "cascadeDelete.previewLoading": "Computing deletion plan…",
        "cascadeDelete.previewError": "Could not compute deletion plan",
        "cascadeDelete.deleteSuccess": `Page deleted. ${String(opts?.count ?? 0)} wikilink(s) cleaned.`,
        "cascadeDelete.deleteError": "Deletion failed",
        "cascadeDelete.noSharedWarnings": "No shared-entity warnings.",
        "cascadeDelete.noPagesDeleted": "Only this page will be deleted.",
        "cascadeDelete.noWikilinksToRewrite": "No other pages reference this page.",
        "cascadeDelete.noRawSource": "No raw source file to delete.",
        "cascadeDelete.occurrences": `${String(opts?.n ?? 0)} occurrence(s)`,
        "common.close": "Close",
        "common.unknown": "Unknown",
      };
      return map[key] ?? key;
    },
    i18n: { language: "en" },
  }),
}));

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const PAGE_ID = "00000000-0000-0000-0000-000000000001";
const PAGE_TITLE = "Test Page";

function makePreview(overrides: Partial<CascadePreviewResponse> = {}): CascadePreviewResponse {
  return {
    target_page_id: PAGE_ID,
    target_title: PAGE_TITLE,
    target_file_path: "wiki/concepts/test-page.md",
    will_delete: [PAGE_ID],
    will_preserve_with_pruned_source: [],
    wikilinks_to_rewrite: [],
    index_entry_will_be_removed: true,
    raw_source_to_delete: null,
    shared_entity_warnings: [],
    match_methods_used: {},
    ...overrides,
  };
}

const DELETE_RESULT: CascadeDeleteResult = {
  deleted_page_id: PAGE_ID,
  wikilinks_cleaned: 2,
  index_entry_removed: true,
  shared_entity_warnings: [],
};

// ─── Default props ─────────────────────────────────────────────────────────────

const defaultProps = {
  pageId: PAGE_ID,
  pageTitle: PAGE_TITLE,
  onDeleted: vi.fn(),
  onCancel: vi.fn(),
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(cascadeDeleteClient.previewCascadeDelete).mockResolvedValue(makePreview());
  vi.mocked(cascadeDeleteClient.cascadeDelete).mockResolvedValue(DELETE_RESULT);
});

// ─── Loading state ────────────────────────────────────────────────────────────

describe("CascadeDeleteModal — loading state", () => {
  it("shows loading text while preview is fetching", () => {
    // Never resolves during this test
    vi.mocked(cascadeDeleteClient.previewCascadeDelete).mockReturnValue(new Promise(() => {}));
    render(<CascadeDeleteModal {...defaultProps} />);
    expect(screen.getByTestId("cascade-delete-preview-loading")).toBeTruthy();
    expect(screen.getByText("Computing deletion plan…")).toBeTruthy();
  });

  it("disables the confirm button while loading", () => {
    vi.mocked(cascadeDeleteClient.previewCascadeDelete).mockReturnValue(new Promise(() => {}));
    render(<CascadeDeleteModal {...defaultProps} />);
    const nextBtn = screen.getByTestId("cascade-delete-next");
    expect((nextBtn as HTMLButtonElement).disabled).toBe(true);
  });
});

// ─── Preview error state ──────────────────────────────────────────────────────

describe("CascadeDeleteModal — preview error", () => {
  it("shows error text when preview fetch fails", async () => {
    vi.mocked(cascadeDeleteClient.previewCascadeDelete).mockRejectedValue(
      new Error("Backend unavailable"),
    );
    render(<CascadeDeleteModal {...defaultProps} />);
    await waitFor(() => {
      expect(screen.getByTestId("cascade-delete-preview-error")).toBeTruthy();
      expect(screen.getByText("Backend unavailable")).toBeTruthy();
    });
  });

  it("keeps the confirm button disabled on error", async () => {
    vi.mocked(cascadeDeleteClient.previewCascadeDelete).mockRejectedValue(new Error("Nope"));
    render(<CascadeDeleteModal {...defaultProps} />);
    await waitFor(() => {
      expect(screen.getByTestId("cascade-delete-preview-error")).toBeTruthy();
    });
    const nextBtn = screen.getByTestId("cascade-delete-next");
    expect((nextBtn as HTMLButtonElement).disabled).toBe(true);
  });
});

// ─── Preview sections ─────────────────────────────────────────────────────────

describe("CascadeDeleteModal — will_delete section", () => {
  it("shows 'only this page' when no other pages will be deleted", async () => {
    vi.mocked(cascadeDeleteClient.previewCascadeDelete).mockResolvedValue(
      makePreview({ will_delete: [PAGE_ID] }),
    );
    render(<CascadeDeleteModal {...defaultProps} />);
    await waitFor(() => {
      expect(screen.getByTestId("cascade-delete-no-extra-pages")).toBeTruthy();
      expect(screen.getByText("Only this page will be deleted.")).toBeTruthy();
    });
  });

  it("lists other pages when will_delete has additional entries", async () => {
    const other1 = "00000000-0000-0000-0000-000000000002";
    vi.mocked(cascadeDeleteClient.previewCascadeDelete).mockResolvedValue(
      makePreview({ will_delete: [PAGE_ID, other1] }),
    );
    render(<CascadeDeleteModal {...defaultProps} />);
    await waitFor(() => {
      expect(screen.getByTestId("cascade-delete-will-delete-list")).toBeTruthy();
      expect(screen.getByText(other1)).toBeTruthy();
    });
  });
});

describe("CascadeDeleteModal — will_preserve section", () => {
  it("does not render the preserve section when list is empty", async () => {
    render(<CascadeDeleteModal {...defaultProps} />);
    await waitFor(() => {
      expect(screen.queryByTestId("cascade-delete-will-preserve-list")).toBeNull();
    });
  });

  it("renders preserved pages when present", async () => {
    const preserved = "00000000-0000-0000-0000-000000000003";
    vi.mocked(cascadeDeleteClient.previewCascadeDelete).mockResolvedValue(
      makePreview({ will_preserve_with_pruned_source: [preserved] }),
    );
    render(<CascadeDeleteModal {...defaultProps} />);
    await waitFor(() => {
      expect(screen.getByTestId("cascade-delete-will-preserve-list")).toBeTruthy();
      expect(screen.getByText(preserved)).toBeTruthy();
    });
  });
});

describe("CascadeDeleteModal — wikilinks section", () => {
  it("shows 'no wikilinks' message when list is empty", async () => {
    render(<CascadeDeleteModal {...defaultProps} />);
    await waitFor(() => {
      expect(screen.getByTestId("cascade-delete-no-wikilinks")).toBeTruthy();
      expect(screen.getByText("No other pages reference this page.")).toBeTruthy();
    });
  });

  it("shows wikilinks list and count when rewrites present", async () => {
    vi.mocked(cascadeDeleteClient.previewCascadeDelete).mockResolvedValue(
      makePreview({
        wikilinks_to_rewrite: [
          {
            source_page_id: "00000000-0000-0000-0000-000000000002",
            file_path: "wiki/concepts/other.md",
            target_title: PAGE_TITLE,
            occurrences: 3,
          },
        ],
      }),
    );
    render(<CascadeDeleteModal {...defaultProps} />);
    await waitFor(() => {
      expect(screen.getByTestId("cascade-delete-wikilinks-list")).toBeTruthy();
      expect(screen.getByText("wiki/concepts/other.md")).toBeTruthy();
    });
  });

  it("shows total occurrences count from multiple rewrites", async () => {
    vi.mocked(cascadeDeleteClient.previewCascadeDelete).mockResolvedValue(
      makePreview({
        wikilinks_to_rewrite: [
          {
            source_page_id: "00000000-0000-0000-0000-000000000002",
            file_path: "wiki/a.md",
            target_title: PAGE_TITLE,
            occurrences: 2,
          },
          {
            source_page_id: "00000000-0000-0000-0000-000000000003",
            file_path: "wiki/b.md",
            target_title: PAGE_TITLE,
            occurrences: 1,
          },
        ],
      }),
    );
    render(<CascadeDeleteModal {...defaultProps} />);
    await waitFor(() => {
      // Total = 3 occurrences
      expect(screen.getByText(/3 dead \[\[wikilinks\]\]/)).toBeTruthy();
    });
  });
});

// ─── Shared-entity warnings (AC-F13-6a) ──────────────────────────────────────

describe("CascadeDeleteModal — shared_entity_warnings (AC-F13-6a)", () => {
  it("shows no warning banner when list is empty", async () => {
    render(<CascadeDeleteModal {...defaultProps} />);
    await waitFor(() => {
      expect(screen.queryByTestId("cascade-delete-shared-warnings")).toBeNull();
    });
  });

  it("shows warning banner prominently when warnings present", async () => {
    vi.mocked(cascadeDeleteClient.previewCascadeDelete).mockResolvedValue(
      makePreview({
        shared_entity_warnings: [
          "Page 'Shared A' shares source overlap with 'Test Page'",
          "Page 'Shared B' shares source overlap with 'Test Page'",
        ],
      }),
    );
    render(<CascadeDeleteModal {...defaultProps} />);
    await waitFor(() => {
      expect(screen.getByTestId("cascade-delete-shared-warnings")).toBeTruthy();
      expect(screen.getByText("Shared-entity warnings")).toBeTruthy();
      expect(
        screen.getByText("Page 'Shared A' shares source overlap with 'Test Page'"),
      ).toBeTruthy();
      expect(
        screen.getByText("Page 'Shared B' shares source overlap with 'Test Page'"),
      ).toBeTruthy();
    });
  });

  it("shows warning banner BEFORE other sections in the DOM (AC-F13-6a)", async () => {
    vi.mocked(cascadeDeleteClient.previewCascadeDelete).mockResolvedValue(
      makePreview({
        shared_entity_warnings: ["Warning A"],
      }),
    );
    render(<CascadeDeleteModal {...defaultProps} />);
    await waitFor(() => {
      const warnBanner = screen.getByTestId("cascade-delete-shared-warnings");
      const wikiSection = screen.queryByTestId("cascade-delete-no-wikilinks");
      expect(warnBanner).toBeTruthy();
      // Warning banner should precede (appear before) the wikilinks section in the DOM
      if (wikiSection) {
        // DOCUMENT_POSITION_FOLLOWING (4): wikiSection follows warnBanner → warnBanner is first
        expect(
          warnBanner.compareDocumentPosition(wikiSection) & Node.DOCUMENT_POSITION_FOLLOWING,
        ).toBeTruthy();
      }
    });
  });
});

// ─── index_entry_will_be_removed indicator ────────────────────────────────────

describe("CascadeDeleteModal — index entry indicator", () => {
  it("shows index entry indicator when true", async () => {
    vi.mocked(cascadeDeleteClient.previewCascadeDelete).mockResolvedValue(
      makePreview({ index_entry_will_be_removed: true }),
    );
    render(<CascadeDeleteModal {...defaultProps} />);
    await waitFor(() => {
      expect(screen.getByTestId("cascade-delete-index-removed")).toBeTruthy();
      expect(screen.getByText("Entry in index.md will be removed")).toBeTruthy();
    });
  });

  it("does not show index indicator when false", async () => {
    vi.mocked(cascadeDeleteClient.previewCascadeDelete).mockResolvedValue(
      makePreview({ index_entry_will_be_removed: false }),
    );
    render(<CascadeDeleteModal {...defaultProps} />);
    await waitFor(() => {
      expect(screen.queryByTestId("cascade-delete-index-removed")).toBeNull();
    });
  });
});

// ─── raw_source_to_delete section ────────────────────────────────────────────

describe("CascadeDeleteModal — raw source section", () => {
  it("shows raw source path when present", async () => {
    vi.mocked(cascadeDeleteClient.previewCascadeDelete).mockResolvedValue(
      makePreview({ raw_source_to_delete: "raw/sources/test.md" }),
    );
    render(<CascadeDeleteModal {...defaultProps} />);
    await waitFor(() => {
      expect(screen.getByTestId("cascade-delete-raw-source")).toBeTruthy();
      expect(screen.getByText("raw/sources/test.md")).toBeTruthy();
    });
  });

  it("does not show raw source section when null", async () => {
    render(<CascadeDeleteModal {...defaultProps} />);
    await waitFor(() => {
      expect(screen.queryByTestId("cascade-delete-raw-source")).toBeNull();
    });
  });
});

// ─── Cancel flow (AC-F13-6d) ─────────────────────────────────────────────────

describe("CascadeDeleteModal — cancel flow", () => {
  it("calls onCancel when Cancel button clicked; no DELETE call made", async () => {
    render(<CascadeDeleteModal {...defaultProps} />);
    await waitFor(() => {
      expect(screen.getByTestId("cascade-delete-cancel")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("cascade-delete-cancel"));
    expect(defaultProps.onCancel).toHaveBeenCalledOnce();
    expect(cascadeDeleteClient.cascadeDelete).not.toHaveBeenCalled();
  });

  it("calls onCancel when ESC key pressed", async () => {
    render(<CascadeDeleteModal {...defaultProps} />);
    await waitFor(() => {
      expect(screen.getByTestId("cascade-delete-modal")).toBeTruthy();
    });
    fireEvent.keyDown(window, { key: "Escape" });
    expect(defaultProps.onCancel).toHaveBeenCalledOnce();
    expect(cascadeDeleteClient.cascadeDelete).not.toHaveBeenCalled();
  });

  it("calls onCancel when clicking the overlay backdrop", async () => {
    render(<CascadeDeleteModal {...defaultProps} />);
    await waitFor(() => {
      expect(screen.getByTestId("cascade-delete-overlay")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("cascade-delete-overlay"));
    expect(defaultProps.onCancel).toHaveBeenCalledOnce();
    expect(cascadeDeleteClient.cascadeDelete).not.toHaveBeenCalled();
  });

  it("calls onCancel when close (✕) button in header clicked", async () => {
    render(<CascadeDeleteModal {...defaultProps} />);
    await waitFor(() => {
      expect(screen.getByTestId("cascade-delete-close")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("cascade-delete-close"));
    expect(defaultProps.onCancel).toHaveBeenCalledOnce();
  });
});

// ─── Step 2: confirm flow ─────────────────────────────────────────────────────

describe("CascadeDeleteModal — confirm flow (step 2)", () => {
  async function goToStep2() {
    // Wait for preview to finish loading, then click next
    await waitFor(() => {
      const nextBtn = screen.getByTestId("cascade-delete-next");
      expect((nextBtn as HTMLButtonElement).disabled).toBe(false);
    });
    fireEvent.click(screen.getByTestId("cascade-delete-next"));
    // Wait for step 2 to render
    await waitFor(() => {
      expect(screen.getByTestId("cascade-delete-confirm")).toBeTruthy();
    });
  }

  it("navigates to step 2 when Next is clicked after preview loads", async () => {
    render(<CascadeDeleteModal {...defaultProps} />);
    await goToStep2();
    // Step 2 title shown
    expect(screen.getByText(/Confirm deletion/)).toBeTruthy();
  });

  it("repeats shared-entity warnings on step 2 confirm screen (AC-F13-6a)", async () => {
    vi.mocked(cascadeDeleteClient.previewCascadeDelete).mockResolvedValue(
      makePreview({ shared_entity_warnings: ["Page 'X' shares source overlap"] }),
    );
    render(<CascadeDeleteModal {...defaultProps} />);
    await goToStep2();
    expect(screen.getByTestId("cascade-delete-confirm-warnings")).toBeTruthy();
    expect(screen.getByText("Page 'X' shares source overlap")).toBeTruthy();
  });

  it("calls cascadeDelete(pageId) on confirm and fires onDeleted", async () => {
    vi.mocked(cascadeDeleteClient.cascadeDelete).mockResolvedValue(DELETE_RESULT);
    render(<CascadeDeleteModal {...defaultProps} />);
    await goToStep2();
    fireEvent.click(screen.getByTestId("cascade-delete-confirm"));
    await waitFor(() => {
      expect(cascadeDeleteClient.cascadeDelete).toHaveBeenCalledWith(PAGE_ID);
    });
    await waitFor(() => {
      expect(defaultProps.onDeleted).toHaveBeenCalledWith(DELETE_RESULT);
    });
  });

  it("shows inline error and does NOT call onDeleted on DELETE failure", async () => {
    vi.mocked(cascadeDeleteClient.cascadeDelete).mockRejectedValue(new Error("Server error: 500"));
    render(<CascadeDeleteModal {...defaultProps} />);
    await goToStep2();
    fireEvent.click(screen.getByTestId("cascade-delete-confirm"));
    await waitFor(() => {
      expect(screen.getByTestId("cascade-delete-error")).toBeTruthy();
      expect(screen.getByText("Server error: 500")).toBeTruthy();
    });
    expect(defaultProps.onDeleted).not.toHaveBeenCalled();
  });

  it("back button on step 2 returns to step 1 (preview)", async () => {
    render(<CascadeDeleteModal {...defaultProps} />);
    await goToStep2();
    fireEvent.click(screen.getByTestId("cascade-delete-back"));
    await waitFor(() => {
      // Back to step 1 — next button should be visible again
      expect(screen.getByTestId("cascade-delete-next")).toBeTruthy();
      expect(screen.queryByTestId("cascade-delete-confirm")).toBeNull();
    });
  });
});

// ─── Summary on step 2 ───────────────────────────────────────────────────────

describe("CascadeDeleteModal — step 2 summary", () => {
  it("shows confirm summary badges after preview loads", async () => {
    vi.mocked(cascadeDeleteClient.previewCascadeDelete).mockResolvedValue(
      makePreview({
        will_delete: [PAGE_ID],
        wikilinks_to_rewrite: [
          {
            source_page_id: "00000000-0000-0000-0000-000000000002",
            file_path: "wiki/a.md",
            target_title: PAGE_TITLE,
            occurrences: 1,
          },
        ],
        index_entry_will_be_removed: true,
      }),
    );
    render(<CascadeDeleteModal {...defaultProps} />);
    // Wait for preview to finish, then move to step 2
    await waitFor(() => {
      const nextBtn = screen.getByTestId("cascade-delete-next");
      expect((nextBtn as HTMLButtonElement).disabled).toBe(false);
    });
    fireEvent.click(screen.getByTestId("cascade-delete-next"));
    await waitFor(() => {
      expect(screen.getByTestId("cascade-delete-confirm-summary")).toBeTruthy();
    });
  });
});
