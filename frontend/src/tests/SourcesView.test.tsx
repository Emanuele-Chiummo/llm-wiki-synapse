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
 *
 * INVARIANT I4: TanStack Virtual mocked for jsdom (no layout engine).
 * INVARIANT I3: mocks return fixed data; no real network calls.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
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

vi.mock("../api/sourcesClient", () => ({
  listSources: vi.fn(),
  getSourceContent: vi.fn(),
  getSourceDerivedPages: vi.fn(),
  deleteSource: vi.fn(),
  sourceRawUrl: (path: string) => `/sources/raw?path=${encodeURIComponent(path)}`,
  triggerIngest: vi.fn(),
}));

import * as sourcesClient from "../api/sourcesClient";

// ─── Mock UploadZone ──────────────────────────────────────────────────────────

vi.mock("../components/ingest/UploadZone", () => ({
  UploadZone: () => <div data-testid="upload-zone">UploadZone</div>,
}));

// ─── Mock i18n ────────────────────────────────────────────────────────────────

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      const map: Record<string, string> = {
        "sources.title": "Sources",
        "sources.import": "Import",
        "sources.refresh": "Refresh",
        "sources.ingest": "Ingest",
        "sources.delete": "Delete",
        "sources.confirmDelete": "Confirm",
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
