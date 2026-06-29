/**
 * ReviewQueueView.test.tsx — vitest + React Testing Library tests for F9 Review Queue UI.
 *
 * Covers:
 *   - Renders header and empty state
 *   - Renders item rows with page_title, item_type badge, pre_generated_query
 *   - Approve action button fires store.approve, item leaves list
 *   - Skip action button fires store.skip, item leaves list
 *   - Deep-Research action fires store.deepResearch; on success shows run_id banner
 *   - 503 banner shows when deepResearchError is set
 *   - Error state rendered when list fetch fails
 *   - Load more button present when items < total
 *
 * All network calls are mocked. Store is reset between tests.
 * INVARIANT I3: store selectors are the API; no direct state mutation in tests.
 * INVARIANT I4: virtualization is present (component uses TanStack Virtual).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ReviewQueueView } from "../components/review/ReviewQueueView";
import { useReviewStore } from "../store/reviewStore";
import type { ReviewItem } from "../api/types";

// ─── Mock TanStack Virtual ────────────────────────────────────────────────────
// In jsdom there is no layout engine; the virtualizer sees zero container height
// and renders no virtual items. We mock it to pass through all items directly.
vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: (opts: { count: number; estimateSize: () => number }) => ({
    getVirtualItems: () =>
      Array.from({ length: opts.count }, (_, i) => ({
        index: i,
        start: i * opts.estimateSize(),
        end: (i + 1) * opts.estimateSize(),
        size: opts.estimateSize(),
        key: i,
        lane: 0,
      })),
    getTotalSize: () => opts.count * opts.estimateSize(),
    measureElement: () => undefined,
    scrollToIndex: () => undefined,
    scrollToOffset: () => undefined,
    scrollRect: { width: 0, height: 600 },
    options: opts,
  }),
}));

// ─── Mock API client ──────────────────────────────────────────────────────────

vi.mock("../api/reviewClient", () => ({
  fetchReviewQueue: vi.fn().mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 }),
  approveReviewItem: vi.fn(),
  skipReviewItem: vi.fn(),
  deepResearchReviewItem: vi.fn(),
}));

import * as reviewClient from "../api/reviewClient";

// ─── Mock i18n ────────────────────────────────────────────────────────────────

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const map: Record<string, string> = {
        "review.title": "Review Queue",
        "review.hint": "Pages recently generated.",
        "review.empty": "No pending items.",
        "review.loadMore": "Load more",
        "review.refresh": "Refresh",
        "review.approve": "Approve",
        "review.skip": "Skip",
        "review.deepResearch": "Deep Research",
        "review.deepResearchStarted": "Deep research run started.",
        "review.viewRun": "View run",
        "review.searxngUnavailable": "Deep research unavailable: SEARXNG_URL not configured.",
        "review.noPage": "(no linked page)",
        "review.noQuery": "No suggested query generated",
        "review.itemType.new_page": "New page",
        "review.itemType.update_page": "Updated",
        "review.itemType.deep_research_candidate": "Research candidate",
        "common.loading": "Loading…",
        "common.retry": "Retry",
        "common.close": "Close",
      };
      return map[key] ?? key;
    },
    i18n: { language: "en" },
  }),
}));

// ─── Mock graphStore ──────────────────────────────────────────────────────────

const mockSetActiveSection = vi.fn();

vi.mock("../store/graphStore", () => ({
  useGraphStore: (selector: (s: unknown) => unknown) =>
    selector({ vaultId: "default", setActiveSection: mockSetActiveSection }),
  selectVaultId: (s: { vaultId: string }) => s.vaultId,
  selectSetActiveSection: (s: { setActiveSection: () => void }) => s.setActiveSection,
}));

// ─── Helpers ──────────────────────────────────────────────────────────────────

function makeItem(id: string, overrides: Partial<ReviewItem> = {}): ReviewItem {
  return {
    id,
    vault_id: "default",
    page_id: `page-${id}`,
    page_title: `Page ${id}`,
    item_type: "new_page",
    status: "pending",
    pre_generated_query: "What is the key concept?",
    deep_research_run_id: null,
    created_at: new Date().toISOString(),
    reviewed_at: null,
    ...overrides,
  };
}

function resetStore(overrides: Partial<ReturnType<typeof useReviewStore.getState>> = {}) {
  useReviewStore.setState({
    items: [],
    total: 0,
    offset: 0,
    loading: false,
    error: null,
    actionInFlight: {},
    actionError: {},
    lastDeepResearch: null,
    deepResearchError: null,
    ...overrides,
  });
}

beforeEach(() => {
  resetStore();
  vi.clearAllMocks();
  vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
    items: [],
    total: 0,
    limit: 50,
    offset: 0,
  });
});

// ─── Rendering ────────────────────────────────────────────────────────────────

describe("ReviewQueueView — rendering", () => {
  it("renders the header with title", async () => {
    render(<ReviewQueueView />);
    // getByText throws if not found — existence is implicitly asserted
    expect(screen.getByText("Review Queue")).toBeTruthy();
  });

  it("renders empty state when no items", async () => {
    render(<ReviewQueueView />);
    await waitFor(() => {
      expect(screen.getByTestId("review-empty")).toBeTruthy();
    });
    expect(screen.getByText("No pending items.")).toBeTruthy();
  });

  it("renders item rows with page_title and action buttons", async () => {
    const items = [makeItem("1"), makeItem("2")];
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items,
      total: 2,
      limit: 50,
      offset: 0,
    });
    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getAllByTestId("review-item-row")).toHaveLength(2);
    });
    expect(screen.getByText("Page 1")).toBeTruthy();
    expect(screen.getByText("Page 2")).toBeTruthy();
  });

  it("renders pre_generated_query first line", async () => {
    const items = [makeItem("1", { pre_generated_query: "First question?\nSecond question?" })];
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items,
      total: 1,
      limit: 50,
      offset: 0,
    });
    render(<ReviewQueueView />);
    await waitFor(() => {
      expect(screen.getByText("First question?")).toBeTruthy();
    });
  });

  it("renders 'no query' placeholder when pre_generated_query is null", async () => {
    const items = [makeItem("1", { pre_generated_query: null })];
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items,
      total: 1,
      limit: 50,
      offset: 0,
    });
    render(<ReviewQueueView />);
    await waitFor(() => {
      expect(screen.getByText("No suggested query generated")).toBeTruthy();
    });
  });

  it("renders item type badge for new_page", async () => {
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [makeItem("1")],
      total: 1,
      limit: 50,
      offset: 0,
    });
    render(<ReviewQueueView />);
    await waitFor(() => {
      expect(screen.getByText("New page")).toBeTruthy();
    });
  });

  it("renders count badge in header when total > 0", async () => {
    const items = [makeItem("1"), makeItem("2")];
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items,
      total: 2,
      limit: 50,
      offset: 0,
    });
    render(<ReviewQueueView />);
    await waitFor(() => {
      expect(screen.getByLabelText("2 pending")).toBeTruthy();
    });
  });

  it("shows load-more button when items < total", async () => {
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [makeItem("1")],
      total: 5,
      limit: 50,
      offset: 0,
    });
    render(<ReviewQueueView />);
    await waitFor(() => {
      expect(screen.getByTestId("review-load-more")).toBeTruthy();
    });
  });

  it("does not show load-more button when all items loaded", async () => {
    resetStore({ items: [makeItem("1")], total: 1 });
    render(<ReviewQueueView />);
    await waitFor(() => {
      expect(screen.queryByTestId("review-load-more")).toBeNull();
    });
  });
});

// ─── Approve action ───────────────────────────────────────────────────────────

describe("ReviewQueueView — approve action", () => {
  it("calls approveReviewItem and item leaves the list", async () => {
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [makeItem("1"), makeItem("2")],
      total: 2,
      limit: 50,
      offset: 0,
    });
    vi.mocked(reviewClient.approveReviewItem).mockResolvedValueOnce(
      makeItem("1", { status: "approved" }),
    );

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getAllByTestId("review-action-approve")).toHaveLength(2);
    });

    fireEvent.click(screen.getAllByTestId("review-action-approve")[0]!);

    await waitFor(() => {
      expect(reviewClient.approveReviewItem).toHaveBeenCalledWith("1");
    });

    await waitFor(() => {
      expect(screen.getAllByTestId("review-item-row")).toHaveLength(1);
      expect(screen.queryByText("Page 1")).toBeNull();
      expect(screen.getByText("Page 2")).toBeTruthy();
    });
  });
});

// ─── Skip action ──────────────────────────────────────────────────────────────

describe("ReviewQueueView — skip action", () => {
  it("calls skipReviewItem and item leaves the list", async () => {
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [makeItem("A"), makeItem("B")],
      total: 2,
      limit: 50,
      offset: 0,
    });
    vi.mocked(reviewClient.skipReviewItem).mockResolvedValueOnce(
      makeItem("A", { status: "skipped" }),
    );

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getAllByTestId("review-action-skip")).toHaveLength(2);
    });

    fireEvent.click(screen.getAllByTestId("review-action-skip")[0]!);

    await waitFor(() => {
      expect(reviewClient.skipReviewItem).toHaveBeenCalledWith("A");
    });

    await waitFor(() => {
      expect(screen.getAllByTestId("review-item-row")).toHaveLength(1);
      expect(screen.queryByText("Page A")).toBeNull();
    });
  });
});

// ─── Deep-research action ─────────────────────────────────────────────────────

describe("ReviewQueueView — deep-research action", () => {
  it("calls deepResearchReviewItem and shows success banner with run_id", async () => {
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [makeItem("X")],
      total: 1,
      limit: 50,
      offset: 0,
    });
    vi.mocked(reviewClient.deepResearchReviewItem).mockResolvedValueOnce({
      review_item_id: "X",
      run_id: "run-abc-12345678",
    });

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getAllByTestId("review-action-deep-research")).toHaveLength(1);
    });

    fireEvent.click(screen.getAllByTestId("review-action-deep-research")[0]!);

    await waitFor(() => {
      expect(reviewClient.deepResearchReviewItem).toHaveBeenCalledWith("X");
    });

    await waitFor(() => {
      expect(screen.getByTestId("review-deep-research-success")).toBeTruthy();
      expect(screen.getByText("Deep research run started.")).toBeTruthy();
      // Shows first 8 chars of run_id
      expect(screen.getByText(/run-abc-/)).toBeTruthy();
    });

    // Item leaves the list
    await waitFor(() => {
      expect(screen.queryByTestId("review-item-row")).toBeNull();
    });
  });

  it("clicking 'View run' navigates to deep-search section", async () => {
    resetStore({
      lastDeepResearch: { itemId: "X", runId: "run-abc-12345678" },
    });

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getByTestId("review-goto-deepsearch")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("review-goto-deepsearch"));
    expect(mockSetActiveSection).toHaveBeenCalledWith("deep-search");
  });
});

// ─── 503 error handling ───────────────────────────────────────────────────────

describe("ReviewQueueView — 503 SEARXNG unavailable", () => {
  it("shows the SEARXNG unavailable banner when deepResearchError is set", async () => {
    resetStore({
      deepResearchError: "503 SEARXNG_URL is not configured",
    });

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getByTestId("review-searxng-error")).toBeTruthy();
      expect(
        screen.getByText("Deep research unavailable: SEARXNG_URL not configured."),
      ).toBeTruthy();
    });
  });

  it("closes the SEARXNG banner when close is clicked", async () => {
    resetStore({
      deepResearchError: "503 SEARXNG_URL is not configured",
    });

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getByTestId("review-searxng-error")).toBeTruthy();
    });

    // Close button is the button inside the banner
    const closeBtns = screen.getAllByText("Close");
    fireEvent.click(closeBtns[0]!);

    await waitFor(() => {
      expect(screen.queryByTestId("review-searxng-error")).toBeNull();
    });
  });

  it("item stays in list after 503 deep-research error", async () => {
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [makeItem("X")],
      total: 1,
      limit: 50,
      offset: 0,
    });
    const { ApiError } = await import("../api/graphClient");
    vi.mocked(reviewClient.deepResearchReviewItem).mockRejectedValueOnce(
      new ApiError(503, "503 SEARXNG_URL is not configured"),
    );

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getAllByTestId("review-action-deep-research")).toHaveLength(1);
    });

    fireEvent.click(screen.getAllByTestId("review-action-deep-research")[0]!);

    await waitFor(() => {
      expect(screen.getByTestId("review-searxng-error")).toBeTruthy();
    });

    // Item still in list
    expect(screen.getByTestId("review-item-row")).toBeTruthy();
  });
});

// ─── Load error ───────────────────────────────────────────────────────────────

describe("ReviewQueueView — list load error", () => {
  it("shows error message when fetch fails", async () => {
    vi.mocked(reviewClient.fetchReviewQueue).mockRejectedValueOnce(
      new Error("Backend unavailable"),
    );

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getByTestId("review-load-error")).toBeTruthy();
      expect(screen.getByText("Backend unavailable")).toBeTruthy();
    });
  });
});
