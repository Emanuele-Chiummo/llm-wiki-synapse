/**
 * NavTreeNewPage.test.tsx — vitest tests for R7-2 "New page from tree" modal.
 *
 * Covers:
 *   - "+" button renders in NavTree header
 *   - Modal opens on click
 *   - Empty title shows validation error on submit
 *   - Successful create calls createPage + closes modal
 *   - 409 conflict shows inline error
 *   - Cancel closes modal without calling createPage
 *
 * INVARIANT I4: TanStack Virtual mocked for jsdom.
 * INVARIANT I3: no real network calls.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { NavTree } from "../components/nav/NavTree";

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

// ─── Mock pagesClient ─────────────────────────────────────────────────────────

vi.mock("../api/pagesClient", () => {
  return {
    fetchAllPages: vi.fn(),
    createPage: vi.fn(),
  };
});

import * as pagesClient from "../api/pagesClient";

// ─── Mock graphClient (ApiError) ─────────────────────────────────────────────

vi.mock("../api/graphClient", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/graphClient")>();
  return { ...actual };
});

// ─── Mock graphStore ──────────────────────────────────────────────────────────

const mockSelectPage = vi.fn();
vi.mock("../store/graphStore", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../store/graphStore")>();
  return {
    ...actual,
    useGraphStore: (selector: (s: unknown) => unknown) =>
      selector({
        selectedNodeId: null,
        selectPage: mockSelectPage,
        toggleGroup: vi.fn(),
        vaultId: "vault-1",
      }),
    useTreeCollapsed: () => ({}),
    selectSelectedNodeId: (s: { selectedNodeId: string | null }) => s.selectedNodeId,
    selectSelectPage: (s: { selectPage: () => void }) => s.selectPage,
    selectToggleGroup: (s: { toggleGroup: () => void }) => s.toggleGroup,
    selectVaultId: (s: { vaultId: string }) => s.vaultId,
  };
});

// ─── Mock i18n ────────────────────────────────────────────────────────────────

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      const map: Record<string, string> = {
        "nav.wiki": "Wiki",
        "nav.newPage.title": "New page",
        "nav.newPage.titleLabel": "Title",
        "nav.newPage.titlePlaceholder": "e.g. My new concept",
        "nav.newPage.titleRequired": "Title is required",
        "nav.newPage.conflict": "A page with this title already exists",
        "nav.newPage.created": "Page created",
        "nav.newPage.typeLabel": "Type",
        "nav.newPage.type.concept": "Concept",
        "nav.newPage.type.entity": "Entity",
        "nav.newPage.type.source": "Source",
        "nav.newPage.type.synthesis": "Synthesis",
        "nav.newPage.type.comparison": "Comparison",
        "nav.newPage.type.query": "Query",
        "nav.newPage.dirLabel": "Directory (optional)",
        "nav.newPage.dirPlaceholder": "e.g. concepts/",
        "nav.newPage.cancel": "Cancel",
        "nav.newPage.create": "Create",
        "nav.comingSoon": "Coming soon",
        "common.loading": "Loading…",
        [`nav.type.concept`]: "Concept",
        [`nav.type.entity`]: "Entity",
        [`nav.type.source`]: "Source",
        [`nav.type.synthesis`]: "Synthesis",
        [`nav.type.comparison`]: "Comparison",
        [`nav.type.query`]: "Query",
        [`nav.type.overview`]: "Overview",
        [`nav.type.other`]: "Other",
      };
      if (params && key === "nav.newPage.created") return `Page created`;
      return map[key] ?? key;
    },
    i18n: { language: "en" },
  }),
}));

// ─── Mock Toast ───────────────────────────────────────────────────────────────

vi.mock("../components/common/Toast", () => ({
  showToast: vi.fn(),
}));

// ─── Helpers ──────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(pagesClient.fetchAllPages).mockResolvedValue({ items: [] });
});

// ─── Tests ────────────────────────────────────────────────────────────────────

describe("NavTree — R7-2 New page modal", () => {
  it('renders "+" button in the wiki header', async () => {
    render(<NavTree vaultId="vault-1" />);
    await waitFor(() => {
      expect(screen.getByTestId("nav-tree-new-page-btn")).toBeTruthy();
    });
  });

  it("opens modal on + click", async () => {
    render(<NavTree vaultId="vault-1" />);
    await waitFor(() => {
      expect(screen.getByTestId("nav-tree-new-page-btn")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("nav-tree-new-page-btn"));

    await waitFor(() => {
      expect(screen.getByText("New page")).toBeTruthy();
    });
  });

  it("shows validation error when title is empty on submit", async () => {
    render(<NavTree vaultId="vault-1" />);
    await waitFor(() => {
      expect(screen.getByTestId("nav-tree-new-page-btn")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("nav-tree-new-page-btn"));
    await waitFor(() => {
      expect(screen.getByText("Create")).toBeTruthy();
    });

    fireEvent.click(screen.getByText("Create"));

    await waitFor(() => {
      expect(screen.getByText("Title is required")).toBeTruthy();
    });
    expect(pagesClient.createPage).not.toHaveBeenCalled();
  });

  it("calls createPage and closes modal on success", async () => {
    vi.mocked(pagesClient.createPage).mockResolvedValue({
      id: "new-page-1",
      file_path: "wiki/concepts/test.md",
      title: "Test page",
      page_type: "concept",
    });

    render(<NavTree vaultId="vault-1" />);
    await waitFor(() => {
      expect(screen.getByTestId("nav-tree-new-page-btn")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("nav-tree-new-page-btn"));
    await waitFor(() => {
      expect(screen.getByPlaceholderText("e.g. My new concept")).toBeTruthy();
    });

    fireEvent.change(screen.getByPlaceholderText("e.g. My new concept"), {
      target: { value: "Test page" },
    });
    fireEvent.click(screen.getByText("Create"));

    await waitFor(() => {
      expect(pagesClient.createPage).toHaveBeenCalledWith({
        title: "Test page",
        page_type: "concept",
        dir: undefined,
        content: undefined,
      });
    });

    // Modal should close after success
    await waitFor(() => {
      expect(screen.queryByText("New page")).toBeNull();
    });
  });

  it("shows conflict error on 409 without closing modal", async () => {
    const { ApiError } = await import("../api/graphClient");
    vi.mocked(pagesClient.createPage).mockRejectedValue(new ApiError(409, "Conflict"));

    render(<NavTree vaultId="vault-1" />);
    await waitFor(() => {
      expect(screen.getByTestId("nav-tree-new-page-btn")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("nav-tree-new-page-btn"));
    await waitFor(() => {
      expect(screen.getByPlaceholderText("e.g. My new concept")).toBeTruthy();
    });

    fireEvent.change(screen.getByPlaceholderText("e.g. My new concept"), {
      target: { value: "Duplicate page" },
    });
    fireEvent.click(screen.getByText("Create"));

    await waitFor(() => {
      expect(screen.getByText("A page with this title already exists")).toBeTruthy();
    });
    // Modal stays open
    expect(screen.getByText("Create")).toBeTruthy();
  });

  it("closes modal on Cancel without calling createPage", async () => {
    render(<NavTree vaultId="vault-1" />);
    await waitFor(() => {
      expect(screen.getByTestId("nav-tree-new-page-btn")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("nav-tree-new-page-btn"));
    await waitFor(() => {
      expect(screen.getByText("Cancel")).toBeTruthy();
    });

    fireEvent.click(screen.getByText("Cancel"));

    await waitFor(() => {
      expect(screen.queryByText("New page")).toBeNull();
    });
    expect(pagesClient.createPage).not.toHaveBeenCalled();
  });
});
