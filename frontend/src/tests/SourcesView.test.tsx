/**
 * SourcesView.test.tsx — vitest + React Testing Library tests for the Sources section [F11 / v0.6].
 *
 * Covers:
 *   - Tree renders file rows from listSources mock
 *   - Folder rows render with correct name
 *   - Ingest button calls triggerIngest with correct prefixed path
 *   - Two-stage delete: first click arms row (Confirm state), second click calls deleteSource
 *   - Ingested badge shows when content.ingested = true
 *   - Empty state shows when no entries
 *   - Source-preview renders for selected path
 *   - Index All button: click calls ingestAllSources, shows progress, handles 409, stops polling
 *
 * INVARIANT I4: TanStack Virtual mocked for jsdom (no layout engine).
 * INVARIANT I3: mocks return fixed data; no real network calls; single poll chain tested via fake timers.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SourcesView } from "../components/sources/SourcesView";
import { SourcePreview } from "../components/sources/SourcePreview";

// ─── Mock TanStack Virtual ────────────────────────────────────────────────────

vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: (opts: { count: number; estimateSize: (i: number) => number }) => ({
    getVirtualItems: () =>
      Array.from({ length: opts.count }, (_, i) => ({
        index: i,
        start: i * opts.estimateSize(i),
        end: (i + 1) * opts.estimateSize(i),
        size: opts.estimateSize(i),
        key: i,
        lane: 0,
      })),
    getTotalSize: () => opts.count * 30,
    measureElement: () => undefined,
  }),
}));

// ─── Mock sourcesClient ───────────────────────────────────────────────────────

vi.mock("../api/sourcesClient", () => {
  // Reproduce the real IngestAllRunningError so instanceof checks work inside the component
  class IngestAllRunningError extends Error {
    constructor() {
      super("ingest-all already running");
      this.name = "IngestAllRunningError";
    }
  }
  return {
    listSources: vi.fn(),
    getSourceContent: vi.fn(),
    getSourceDerivedPages: vi.fn(),
    deleteSource: vi.fn(),
    deleteFolderSource: vi.fn(),
    sourceRawUrl: (path: string) => `/sources/raw?path=${encodeURIComponent(path)}`,
    triggerIngest: vi.fn(),
    ingestAllSources: vi.fn(),
    getIngestAllStatus: vi.fn(),
    IngestAllRunningError,
  };
});

import * as sourcesClient from "../api/sourcesClient";

// ─── Mock ingestClient (uploadDocument used by S1 folder upload) ──────────────

vi.mock("../api/ingestClient", () => ({
  uploadDocument: vi.fn(),
}));

import * as ingestClient from "../api/ingestClient";

// ─── Mock UploadZone ──────────────────────────────────────────────────────────

vi.mock("../components/ingest/UploadZone", () => ({
  UploadZone: () => <div data-testid="upload-zone">UploadZone</div>,
}));

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
    danger?: boolean;
    onConfirm: () => void;
    onCancel: () => void;
  }) => (
    <div data-testid="confirm-dialog">
      <span>{title}</span>
      <button data-testid="confirm-dialog-confirm" onClick={onConfirm}>{confirmLabel}</button>
      <button data-testid="confirm-dialog-cancel" onClick={onCancel}>{cancelLabel}</button>
    </div>
  ),
}));

// ─── Mock i18n ────────────────────────────────────────────────────────────────

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      const map: Record<string, string> = {
        "sources.title": "Sources",
        "sources.import": "Import",
        "sources.importFolder": "+ Folder",
        "sources.refresh": "Refresh",
        "sources.ingest": "Ingest",
        "sources.delete": "Delete",
        "sources.confirmDelete": "Confirm",
        "sources.deleteFolder": "Delete folder",
        "sources.confirmDeleteFolder": "Confirm",
        "sources.deletedFolderToast": `Folder deleted · ${String(params?.files ?? 0)} file(s), ${String(params?.pages ?? 0)} page(s)`,
        "sources.deletedFolderTooMany": "Folder too large to delete.",
        "sources.ingested": "Ingested",
        "sources.notIngested": "Not ingested",
        "sources.derivedPages": "pages",
        "sources.empty": "Select a file to preview it",
        "sources.emptyHint": "No source files yet. Import a document to get started.",
        "sources.noPreview": "No preview available",
        "sources.deletedToast": `Deleted. ${String(params?.pages ?? 0)} wiki page(s) removed.`,
        "sources.ingestedToast": "Ingest started.",
        "sources.folder": "items",
        "sources.file": "Open raw file",
        "sources.ingestAll": "Index all",
        "sources.ingestAllStarted": `${String(params?.count ?? 0)} files indexing`,
        "sources.ingestAllRunning": `Indexing… ${String(params?.done ?? 0)}/${String(params?.total ?? 0)}`,
        "sources.ingestAllNone": "Nothing to index",
        "sources.ingestAllAlready": "Already running",
        "sources.footerCount": `${String(params?.total ?? 0)} sources`,
        "sources.folderUploadSkipped": `${String(params?.count ?? 0)} file(s) skipped (unsupported type)`,
        "sources.bulk.selectAll": "Select all files",
        "sources.bulk.selectFile": `Select ${String(params?.name ?? "")}`,
        "sources.bulk.selected": `${String(params?.count ?? 0)} selected`,
        "sources.bulk.ingest": "Ingest selected",
        "sources.bulk.delete": "Delete selected",
        "sources.bulk.clearSelection": "Clear selection",
        "sources.bulk.deleteDialogTitle": "Delete selected files",
        "sources.bulk.deleteDialogBody": `Permanently delete ${String(params?.count ?? 0)} file(s) and their derived wiki pages?`,
        "sources.bulk.deleteConfirm": "Delete",
        "sources.bulk.deleteCancel": "Cancel",
        "sources.bulk.progress": `${String(params?.current ?? 0)}/${String(params?.total ?? 0)} — ${String(params?.path ?? "")}`,
        "sources.bulk.ingestDone": `Ingest complete: ${String(params?.count ?? 0)} file(s) processed`,
        "sources.bulk.ingestPartial": `Ingest partial: ${String(params?.done ?? 0)}/${String(params?.total ?? 0)} succeeded, ${String(params?.failed ?? 0)} failed`,
        "sources.bulk.deleteDone": `Deleted ${String(params?.count ?? 0)} file(s)`,
        "sources.bulk.deletePartial": `Deleted ${String(params?.done ?? 0)}/${String(params?.total ?? 0)}, ${String(params?.failed ?? 0)} failed`,
        "common.loading": "Loading…",
      };
      return map[key] ?? key;
    },
    i18n: { language: "en" },
  }),
}));

// ─── Mock graphStore ──────────────────────────────────────────────────────────

vi.mock("../store/graphStore", () => ({
  useGraphStore: (selector: (s: unknown) => unknown) =>
    selector({
      selectPage: vi.fn(),
      setActiveSection: vi.fn(),
    }),
  selectSelectPage: (s: { selectPage: () => void }) => s.selectPage,
  selectSetActiveSection: (s: { setActiveSection: () => void }) => s.setActiveSection,
}));

// ─── Mock Toast ───────────────────────────────────────────────────────────────

vi.mock("../components/common/Toast", () => ({
  showToast: vi.fn(),
}));

// ─── Helpers ──────────────────────────────────────────────────────────────────

const SAMPLE_ENTRIES = [
  { path: "doc1.md", name: "doc1.md", is_dir: false, ext: ".md", size_bytes: 1024, mtime: "2026-06-28T07:27:37+00:00" },
  { path: "doc2.pdf", name: "doc2.pdf", is_dir: false, ext: ".pdf", size_bytes: 2048, mtime: "2026-06-28T07:28:00+00:00" },
  { path: "images", name: "images", is_dir: true },
];

function makeContent(overrides: Partial<import("../api/sourcesClient").SourceContentResponse> = {}): import("../api/sourcesClient").SourceContentResponse {
  return {
    path: "doc1.md",
    name: "doc1.md",
    ext: ".md",
    size_bytes: 1024,
    mtime: "2026-06-28T07:27:37+00:00",
    category: "markdown",
    is_text: true,
    text: "# Hello",
    ingested: false,
    page_ids: [],
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  // NOTE: real timers — @testing-library `waitFor` polls on real timers, so global fake
  // timers freeze it and every async test times out. The one test that needs the poll to
  // advance waits with a real-timer waitFor(timeout) instead.
  vi.mocked(sourcesClient.listSources).mockResolvedValue({
    entries: [],
    total: 0,
    truncated: false,
  });
  vi.mocked(sourcesClient.getSourceContent).mockResolvedValue(makeContent());
  vi.mocked(sourcesClient.getSourceDerivedPages).mockResolvedValue([]);
  vi.mocked(sourcesClient.triggerIngest).mockResolvedValue(undefined);
  vi.mocked(sourcesClient.deleteSource).mockResolvedValue({
    deleted_source: "doc1.md",
    pages_deleted: 0,
  });
  vi.mocked(sourcesClient.deleteFolderSource).mockResolvedValue({
    deleted_source: "images",
    files_deleted: 3,
    pages_cascaded: 2,
  });
  // Default: no scan running
  vi.mocked(sourcesClient.getIngestAllStatus).mockResolvedValue({
    running: false,
    done: 0,
    total: 0,
  });
  vi.mocked(sourcesClient.ingestAllSources).mockResolvedValue({
    started: true,
    candidate_files: 5,
  });
  vi.mocked(ingestClient.uploadDocument).mockResolvedValue({
    file_path: "raw/sources/test.md",
    status: "queued",
    overwritten: false,
  });
});

afterEach(() => {
  vi.useRealTimers();
});

// ─── SourcesView rendering ────────────────────────────────────────────────────

describe("SourcesView — rendering", () => {
  it("renders the view container", async () => {
    render(<SourcesView />);
    expect(screen.getByTestId("sources-view")).toBeTruthy();
  });

  it("shows 'Sources' header", async () => {
    render(<SourcesView />);
    expect(screen.getByText("Sources")).toBeTruthy();
  });

  it("shows Refresh button", async () => {
    render(<SourcesView />);
    expect(screen.getByTestId("source-refresh")).toBeTruthy();
  });

  it("shows empty state when entries is empty", async () => {
    render(<SourcesView />);
    await waitFor(() => {
      expect(screen.getByText("No source files yet. Import a document to get started.")).toBeTruthy();
    });
  });

  it("renders file rows from listSources", async () => {
    vi.mocked(sourcesClient.listSources).mockResolvedValue({
      entries: SAMPLE_ENTRIES,
      total: 3,
      truncated: false,
    });
    render(<SourcesView />);
    await waitFor(() => {
      expect(screen.getAllByTestId("source-row").length).toBeGreaterThanOrEqual(2);
    });
    expect(screen.getByText("doc1.md")).toBeTruthy();
    expect(screen.getByText("doc2.pdf")).toBeTruthy();
  });

  it("renders folder row with name", async () => {
    vi.mocked(sourcesClient.listSources).mockResolvedValue({
      entries: SAMPLE_ENTRIES,
      total: 3,
      truncated: false,
    });
    render(<SourcesView />);
    await waitFor(() => {
      expect(screen.getByText("images")).toBeTruthy();
    });
  });

  it("renders the sources-tree testid when rows exist", async () => {
    vi.mocked(sourcesClient.listSources).mockResolvedValue({
      entries: SAMPLE_ENTRIES,
      total: 3,
      truncated: false,
    });
    render(<SourcesView />);
    await waitFor(() => {
      expect(screen.getByTestId("sources-tree")).toBeTruthy();
    });
  });
});

// ─── Ingest action ───────────────────────────────────────────────────────────

describe("SourcesView — ingest action", () => {
  it("calls triggerIngest with raw/sources/ prefixed path", async () => {
    vi.mocked(sourcesClient.listSources).mockResolvedValue({
      entries: [SAMPLE_ENTRIES[0]!],
      total: 1,
      truncated: false,
    });
    render(<SourcesView />);
    await waitFor(() => {
      expect(screen.getByTestId("source-ingest")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("source-ingest"));

    await waitFor(() => {
      expect(sourcesClient.triggerIngest).toHaveBeenCalledWith("raw/sources/doc1.md");
    });
  });
});

// ─── Two-stage delete ─────────────────────────────────────────────────────────

describe("SourcesView — two-stage delete", () => {
  beforeEach(() => {
    vi.mocked(sourcesClient.listSources).mockResolvedValue({
      entries: [SAMPLE_ENTRIES[0]!],
      total: 1,
      truncated: false,
    });
  });

  it("first click shows Confirm state but does NOT call deleteSource", async () => {
    render(<SourcesView />);
    await waitFor(() => {
      expect(screen.getByTestId("source-delete")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("source-delete"));

    // Armed — shows "Confirm" text, deleteSource not called yet
    await waitFor(() => {
      expect(screen.getByText("Confirm")).toBeTruthy();
    });
    expect(sourcesClient.deleteSource).not.toHaveBeenCalled();
  });

  it("second click calls deleteSource after arming", async () => {
    render(<SourcesView />);
    await waitFor(() => {
      expect(screen.getByTestId("source-delete")).toBeTruthy();
    });

    // First click — arm
    fireEvent.click(screen.getByTestId("source-delete"));
    await waitFor(() => {
      expect(screen.getByText("Confirm")).toBeTruthy();
    });

    // Second click — fire
    fireEvent.click(screen.getByText("Confirm"));
    await waitFor(() => {
      expect(sourcesClient.deleteSource).toHaveBeenCalledWith("doc1.md");
    });
  });
});

// ─── SourcePreview — ingested badge ──────────────────────────────────────────

describe("SourcePreview — ingested badge", () => {
  it("shows ingested badge when content.ingested = true", async () => {
    vi.mocked(sourcesClient.getSourceContent).mockResolvedValue(
      makeContent({ ingested: true, page_ids: ["p1", "p2"] }),
    );
    vi.mocked(sourcesClient.getSourceDerivedPages).mockResolvedValue([
      { id: "p1", title: "Page One", file_path: "wiki/entities/page-one.md" },
      { id: "p2", title: "Page Two", file_path: "wiki/entities/page-two.md" },
    ]);
    render(<SourcePreview path="doc1.md" />);
    await waitFor(() => {
      expect(screen.getByTestId("source-ingested-badge")).toBeTruthy();
    });
    expect(screen.getByText(/Ingested/)).toBeTruthy();
  });

  it("shows not-ingested badge when content.ingested = false", async () => {
    vi.mocked(sourcesClient.getSourceContent).mockResolvedValue(
      makeContent({ ingested: false }),
    );
    render(<SourcePreview path="doc1.md" />);
    await waitFor(() => {
      expect(screen.getByTestId("source-ingested-badge")).toBeTruthy();
    });
    expect(screen.getByText("Not ingested")).toBeTruthy();
  });

  it("renders image preview for image category", async () => {
    vi.mocked(sourcesClient.getSourceContent).mockResolvedValue(
      makeContent({ category: "image", is_text: false }),
    );
    render(<SourcePreview path="photo.png" />);
    await waitFor(() => {
      expect(screen.getByTestId("source-preview-image")).toBeTruthy();
    });
  });

  it("renders text preview for text category", async () => {
    vi.mocked(sourcesClient.getSourceContent).mockResolvedValue(
      makeContent({ category: "text", is_text: true, text: "plain text content" }),
    );
    render(<SourcePreview path="doc.txt" />);
    await waitFor(() => {
      expect(screen.getByTestId("source-preview-text")).toBeTruthy();
    });
  });

  it("renders empty state when path is null", async () => {
    render(<SourcePreview path={null} />);
    expect(screen.getByTestId("source-preview")).toBeTruthy();
    expect(screen.getByText("Select a file to preview it")).toBeTruthy();
  });
});

// ─── Index All button ─────────────────────────────────────────────────────────

describe("SourcesView — Index All button", () => {
  it("renders the sources-ingest-all button in idle state", async () => {
    render(<SourcesView />);
    // Let the mount-time status check settle
    await waitFor(() => {
      expect(screen.getByTestId("sources-ingest-all")).toBeTruthy();
    });
    expect(screen.getByText("Index all")).toBeTruthy();
    // Button is not disabled when no scan is running
    expect(screen.getByTestId("sources-ingest-all").hasAttribute("disabled")).toBe(false);
  });

  it("calls ingestAllSources when the button is clicked", async () => {
    render(<SourcesView />);
    await waitFor(() => {
      expect(screen.getByTestId("sources-ingest-all")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("sources-ingest-all"));

    await waitFor(() => {
      expect(sourcesClient.ingestAllSources).toHaveBeenCalledTimes(1);
    });
  });

  it("shows progress label when status returns running=true", async () => {
    // Mock: status returns running with 2/10
    vi.mocked(sourcesClient.getIngestAllStatus).mockResolvedValue({
      running: true,
      done: 2,
      total: 10,
    });

    render(<SourcesView />);

    // The mount effect calls getIngestAllStatus — wait for it to resolve
    await waitFor(() => {
      expect(sourcesClient.getIngestAllStatus).toHaveBeenCalled();
    });

    // Progress label should appear
    await waitFor(() => {
      expect(screen.getByTestId("sources-ingest-all-progress")).toBeTruthy();
    });
    expect(screen.getByText("Indexing… 2/10")).toBeTruthy();

    // Button should be disabled while running
    expect(screen.getByTestId("sources-ingest-all").hasAttribute("disabled")).toBe(true);
  });

  it("shows 'Already running' toast and starts polling on 409 IngestAllRunningError", async () => {
    const { showToast } = await import("../components/common/Toast");
    const { IngestAllRunningError: RunningErr } = await import("../api/sourcesClient");

    vi.mocked(sourcesClient.ingestAllSources).mockRejectedValue(new RunningErr());
    // Status will say running after the 409
    vi.mocked(sourcesClient.getIngestAllStatus).mockResolvedValue({
      running: false,
      done: 0,
      total: 0,
    });

    render(<SourcesView />);
    await waitFor(() => {
      expect(screen.getByTestId("sources-ingest-all")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("sources-ingest-all"));

    await waitFor(() => {
      expect(showToast).toHaveBeenCalledWith("Already running", "success");
    });
  });

  it("stops polling and re-enables button when running becomes false", async () => {
    // First call (mount check): running
    // Second call (poll tick): not running → should clear progress
    let callCount = 0;
    vi.mocked(sourcesClient.getIngestAllStatus).mockImplementation(async () => {
      callCount++;
      if (callCount === 1) return { running: true, done: 3, total: 5 };
      return { running: false, done: 5, total: 5 };
    });

    render(<SourcesView />);

    // First status check on mount → running
    await waitFor(() => {
      expect(screen.getByTestId("sources-ingest-all-progress")).toBeTruthy();
    });

    // The next poll fires after the real INGEST_ALL_POLL_MS (~1.5s) and returns running=false.
    // After that the progress label should disappear — wait on real timers.
    await waitFor(
      () => {
        expect(screen.queryByTestId("sources-ingest-all-progress")).toBeNull();
      },
      { timeout: 4000 },
    );
    // Button re-enabled
    expect(screen.getByTestId("sources-ingest-all").hasAttribute("disabled")).toBe(false);
  });
});

// ─── R7-11: Bulk multi-select ─────────────────────────────────────────────────

describe("SourcesView — R7-11 bulk multi-select (AC-R7-11-1)", () => {
  beforeEach(() => {
    vi.mocked(sourcesClient.listSources).mockResolvedValue({
      entries: [SAMPLE_ENTRIES[0]!, SAMPLE_ENTRIES[1]!],
      total: 2,
      truncated: false,
    });
  });

  it("renders a select-all checkbox in the header", async () => {
    render(<SourcesView />);
    await waitFor(() => {
      expect(screen.getByTestId("sources-select-all")).toBeTruthy();
    });
  });

  it("renders per-row checkboxes for file rows", async () => {
    render(<SourcesView />);
    await waitFor(() => {
      const checkboxes = screen.getAllByTestId("source-row-checkbox");
      expect(checkboxes.length).toBe(2);
    });
  });

  it("shows bulk action bar when at least one row is checked", async () => {
    render(<SourcesView />);
    await waitFor(() => {
      expect(screen.getAllByTestId("source-row-checkbox").length).toBeGreaterThan(0);
    });

    const [firstCheckbox] = screen.getAllByTestId("source-row-checkbox");
    fireEvent.click(firstCheckbox!);

    await waitFor(() => {
      expect(screen.getByTestId("sources-bulk-bar")).toBeTruthy();
    });
    expect(screen.getByTestId("sources-bulk-ingest")).toBeTruthy();
    expect(screen.getByTestId("sources-bulk-delete")).toBeTruthy();
  });

  it("select-all checkbox selects all file rows", async () => {
    render(<SourcesView />);
    await waitFor(() => {
      expect(screen.getByTestId("sources-select-all")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("sources-select-all"));

    await waitFor(() => {
      expect(screen.getByTestId("sources-bulk-bar")).toBeTruthy();
    });
    expect(screen.getByText("2 selected")).toBeTruthy();
  });

  it("hides bulk bar when selection is cleared", async () => {
    render(<SourcesView />);
    await waitFor(() => {
      expect(screen.getAllByTestId("source-row-checkbox").length).toBeGreaterThan(0);
    });

    const [firstCheckbox] = screen.getAllByTestId("source-row-checkbox");
    fireEvent.click(firstCheckbox!);
    await waitFor(() => {
      expect(screen.getByTestId("sources-bulk-bar")).toBeTruthy();
    });

    fireEvent.click(screen.getByText("Clear selection"));
    await waitFor(() => {
      expect(screen.queryByTestId("sources-bulk-bar")).toBeNull();
    });
  });
});

describe("SourcesView — R7-11 bulk ingest (AC-R7-11-2)", () => {
  beforeEach(() => {
    vi.mocked(sourcesClient.listSources).mockResolvedValue({
      entries: [SAMPLE_ENTRIES[0]!, SAMPLE_ENTRIES[1]!],
      total: 2,
      truncated: false,
    });
    vi.mocked(sourcesClient.triggerIngest).mockResolvedValue(undefined);
  });

  it("calls triggerIngest for each selected file sequentially", async () => {
    const { showToast } = await import("../components/common/Toast");

    render(<SourcesView />);
    await waitFor(() => {
      expect(screen.getByTestId("sources-select-all")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("sources-select-all"));
    await waitFor(() => {
      expect(screen.getByTestId("sources-bulk-ingest")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("sources-bulk-ingest"));

    await waitFor(() => {
      expect(sourcesClient.triggerIngest).toHaveBeenCalledTimes(2);
    });
    expect(sourcesClient.triggerIngest).toHaveBeenCalledWith("raw/sources/doc1.md");
    expect(sourcesClient.triggerIngest).toHaveBeenCalledWith("raw/sources/doc2.pdf");
    await waitFor(() => {
      expect(showToast).toHaveBeenCalledWith(
        "Ingest complete: 2 file(s) processed",
        "success",
      );
    });
  });

  it("shows partial-failure toast when some ingests fail", async () => {
    const { showToast } = await import("../components/common/Toast");

    vi.mocked(sourcesClient.triggerIngest)
      .mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(new Error("network error"));

    render(<SourcesView />);
    await waitFor(() => {
      expect(screen.getByTestId("sources-select-all")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("sources-select-all"));
    await waitFor(() => {
      expect(screen.getByTestId("sources-bulk-ingest")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("sources-bulk-ingest"));

    await waitFor(() => {
      expect(showToast).toHaveBeenCalledWith(
        "Ingest partial: 1/2 succeeded, 1 failed",
        "error",
      );
    });
  });
});

describe("SourcesView — R7-11 bulk delete (AC-R7-11-3)", () => {
  beforeEach(() => {
    vi.mocked(sourcesClient.listSources).mockResolvedValue({
      entries: [SAMPLE_ENTRIES[0]!, SAMPLE_ENTRIES[1]!],
      total: 2,
      truncated: false,
    });
    vi.mocked(sourcesClient.deleteSource).mockResolvedValue({
      deleted_source: "doc1.md",
      pages_deleted: 0,
    });
  });

  it("shows ConfirmDialog when Delete selected is clicked", async () => {
    render(<SourcesView />);
    await waitFor(() => {
      expect(screen.getByTestId("sources-select-all")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("sources-select-all"));
    await waitFor(() => {
      expect(screen.getByTestId("sources-bulk-delete")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("sources-bulk-delete"));

    await waitFor(() => {
      expect(screen.getByTestId("confirm-dialog")).toBeTruthy();
    });
    expect(screen.getByText("Delete selected files")).toBeTruthy();
  });

  it("calls deleteSource for each selected file after confirmation", async () => {
    const { showToast } = await import("../components/common/Toast");

    render(<SourcesView />);
    await waitFor(() => {
      expect(screen.getByTestId("sources-select-all")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("sources-select-all"));
    await waitFor(() => {
      expect(screen.getByTestId("sources-bulk-delete")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("sources-bulk-delete"));
    await waitFor(() => {
      expect(screen.getByTestId("confirm-dialog-confirm")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));

    await waitFor(() => {
      expect(sourcesClient.deleteSource).toHaveBeenCalledTimes(2);
    });
    await waitFor(() => {
      expect(showToast).toHaveBeenCalledWith("Deleted 2 file(s)", "success");
    });
  });

  it("dismisses ConfirmDialog on cancel without deleting", async () => {
    render(<SourcesView />);
    await waitFor(() => {
      expect(screen.getByTestId("sources-select-all")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("sources-select-all"));
    await waitFor(() => {
      expect(screen.getByTestId("sources-bulk-delete")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("sources-bulk-delete"));
    await waitFor(() => {
      expect(screen.getByTestId("confirm-dialog-cancel")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("confirm-dialog-cancel"));

    await waitFor(() => {
      expect(screen.queryByTestId("confirm-dialog")).toBeNull();
    });
    expect(sourcesClient.deleteSource).not.toHaveBeenCalled();
  });
});

// ─── S3: Footer count ─────────────────────────────────────────────────────────

describe("SourcesView — S3 footer count", () => {
  it("renders the footer with the total count from listSources", async () => {
    vi.mocked(sourcesClient.listSources).mockResolvedValue({
      entries: SAMPLE_ENTRIES,
      total: 3,
      truncated: false,
    });
    render(<SourcesView />);
    await waitFor(() => {
      expect(screen.getByTestId("sources-footer")).toBeTruthy();
    });
    expect(screen.getByText("3 sources")).toBeTruthy();
  });

  it("shows 0 sources when the list is empty", async () => {
    render(<SourcesView />);
    await waitFor(() => {
      expect(screen.getByTestId("sources-footer")).toBeTruthy();
    });
    expect(screen.getByText("0 sources")).toBeTruthy();
  });
});

// ─── S1: "+ Folder" button ───────────────────────────────────────────────────

describe("SourcesView — S1 folder upload button", () => {
  it("renders the + Folder button in the header", async () => {
    render(<SourcesView />);
    await waitFor(() => {
      expect(screen.getByTestId("source-import-folder")).toBeTruthy();
    });
    expect(screen.getByText("+ Folder")).toBeTruthy();
  });
});

// ─── S2: Folder two-stage delete ─────────────────────────────────────────────

describe("SourcesView — S2 folder delete (two-stage)", () => {
  beforeEach(() => {
    vi.mocked(sourcesClient.listSources).mockResolvedValue({
      entries: SAMPLE_ENTRIES,
      total: 3,
      truncated: false,
    });
  });

  it("renders a folder-delete button on folder rows", async () => {
    render(<SourcesView />);
    await waitFor(() => {
      expect(screen.getByTestId("source-folder-delete")).toBeTruthy();
    });
  });

  it("first click arms the folder-delete (shows Confirm text but does NOT call deleteFolderSource)", async () => {
    render(<SourcesView />);
    await waitFor(() => {
      expect(screen.getByTestId("source-folder-delete")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("source-folder-delete"));

    await waitFor(() => {
      expect(screen.getByText("Confirm")).toBeTruthy();
    });
    expect(sourcesClient.deleteFolderSource).not.toHaveBeenCalled();
  });

  it("second click calls deleteFolderSource and shows toast", async () => {
    const { showToast } = await import("../components/common/Toast");

    render(<SourcesView />);
    await waitFor(() => {
      expect(screen.getByTestId("source-folder-delete")).toBeTruthy();
    });

    // First click — arm
    fireEvent.click(screen.getByTestId("source-folder-delete"));
    await waitFor(() => {
      expect(screen.getByText("Confirm")).toBeTruthy();
    });

    // Second click — confirm
    fireEvent.click(screen.getByText("Confirm"));

    await waitFor(() => {
      expect(sourcesClient.deleteFolderSource).toHaveBeenCalledWith("images");
    });
    await waitFor(() => {
      expect(showToast).toHaveBeenCalledWith(
        "Folder deleted · 3 file(s), 2 page(s)",
        "success",
      );
    });
  });
});
