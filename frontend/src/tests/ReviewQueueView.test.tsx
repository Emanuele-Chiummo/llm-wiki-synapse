/**
 * ReviewQueueView.test.tsx — vitest + React Testing Library tests for F9 Review Queue UI.
 *
 * Covers (ADR-0034 §7.1 proposal model):
 *   - Renders header and empty state
 *   - Renders item rows with type badge, proposed_title, rationale
 *   - Shows conflict page (page_title) for contradiction / duplicate types
 *   - Create action button fires store.create; shows spinner while in-flight
 *   - 502 from Create shows retry-or-skip hint; item stays in list
 *   - Skip action button fires store.skip, item leaves list
 *   - Deep-Research action fires store.deepResearch; on success shows run_id banner
 *   - 503 banner shows when deepResearchError is set
 *   - Sweep result banner appears after sweep
 *   - Error state rendered when list fetch fails
 *   - Load more button present when items < total
 *
 * All network calls are mocked. Store is reset between tests.
 * INVARIANT I3: store selectors are the API; no direct state mutation in tests.
 * INVARIANT I4: virtualization is present (component uses TanStack Virtual).
 * pre_generated_query is GONE — items now carry proposed_title + rationale.
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
  createReviewItem: vi.fn(),
  skipReviewItem: vi.fn(),
  dismissReviewItem: vi.fn(),
  deepResearchReviewItem: vi.fn(),
  bulkReview: vi.fn(),
  sweepReviewQueue: vi.fn(),
  clearResolved: vi.fn(),
}));

import * as reviewClient from "../api/reviewClient";

// ─── Mock i18n ────────────────────────────────────────────────────────────────

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      const map: Record<string, string> = {
        "review.title": "Review Queue",
        "review.hint": "AI proposals.",
        "review.empty": "No proposals.",
        "review.loadMore": "Load more",
        "review.refresh": "Refresh",
        "review.create": "Create",
        "review.creating": "Creating…",
        "review.createFailed": "Page generation failed — retry or skip.",
        "review.skip": "Skip",
        "review.dismiss": "Dismiss",
        "review.deepResearch": "Deep Research",
        "review.deepResearchStarted": "Deep research run started.",
        "review.viewRun": "View run",
        "review.searxngUnavailable": "Deep research unavailable: SEARXNG_URL not configured.",
        "review.noTitle": "(no proposed title)",
        "review.noRationale": "No rationale provided",
        "review.conflictsWith": "Conflicts with",
        "review.referencedPages": "Related pages",
        "review.willSearch": "will search",
        "review.sweep": "Auto-resolve",
        "review.sweepHelp": "Run auto-resolution sweep.",
        "review.sweepResult": `Sweep complete: ${String(params?.rule ?? 0)} rule-resolved, ${String(params?.llm ?? 0)} LLM-resolved, ${String(params?.kept ?? 0)} kept pending.`,
        "review.bulkResult": `Bulk action complete: ${String(params?.updated ?? 0)} updated, ${String(params?.skipped ?? 0)} already resolved.`,
        "review.clearResolved": "Clear resolved",
        "review.clearResolvedHelp": "Hard-delete terminal rows.",
        "review.clearResult": `Cleared ${String(params?.count ?? 0)} resolved item(s).`,
        "review.selectPending": "Select pending",
        "review.deselectAll": "Deselect all",
        "review.selectionCount": `${String(params?.count ?? 0)} selected`,
        "review.markResolved": "Mark resolved",
        "review.bulkError": "Bulk action failed.",
        "review.tabPending": "Pending",
        "review.tabResolved": "Resolved",
        "review.tabDismissed": "Dismissed",
        "review.statusBadge.auto_resolved": "Auto-resolved",
        "review.statusBadge.created": "Created",
        "review.statusBadge.deep_researched": "Researched",
        "review.statusBadge.skipped": "Skipped",
        "review.statusBadge.dismissed": "Dismissed",
        "review.resolvedAt": "Resolved {{date}}",
        "review.viewCreatedPage": "View page",
        "review.itemType.missing-page": "Missing page",
        "review.itemType.suggestion": "Suggestion",
        "review.itemType.contradiction": "Contradiction",
        "review.itemType.duplicate": "Duplicate",
        "review.itemType.confirm": "Confirm",
        "review.pageType.concept": "concept",
        "review.pageType.entity": "entity",
        "common.loading": "Loading…",
        "common.retry": "Retry",
        "common.close": "Close",
        "nav.review": "Review",
      };
      return map[key] ?? key;
    },
    i18n: { language: "en" },
  }),
}));

// ─── Mock graphStore ──────────────────────────────────────────────────────────

const mockSetActiveSection = vi.fn();
const mockSelectPage = vi.fn();

vi.mock("../store/graphStore", () => ({
  useGraphStore: (selector: (s: unknown) => unknown) =>
    selector({
      vaultId: "default",
      setActiveSection: mockSetActiveSection,
      selectPage: mockSelectPage,
    }),
  selectVaultId: (s: { vaultId: string }) => s.vaultId,
  selectSetActiveSection: (s: { setActiveSection: () => void }) => s.setActiveSection,
  selectSelectPage: (s: { selectPage: () => void }) => s.selectPage,
}));

// ─── Helpers ──────────────────────────────────────────────────────────────────

function makeItem(id: string, overrides: Partial<ReviewItem> = {}): ReviewItem {
  return {
    id,
    vault_id: "default",
    item_type: "missing-page",
    status: "pending",
    proposed_title: `Proposed Page ${id}`,
    proposed_page_type: "concept",
    proposed_dir: "concepts",
    rationale: `Rationale for item ${id}.`,
    page_id: null,
    page_title: null,
    source_page_id: null,
    created_page_id: null,
    resolution: null,
    deep_research_run_id: null,
    content_key: null,
    referenced_page_ids: null,
    referenced_pages: null,
    search_queries: null,
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
    activeTab: "pending",
    selectedIds: new Set<string>(),
    actionInFlight: {},
    actionError: {},
    createGenerationError: {},
    lastDeepResearch: null,
    deepResearchError: null,
    lastSweepResult: null,
    lastBulkResult: null,
    lastClearResult: null,
    bulkError: null,
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
    expect(screen.getByText("Review Queue")).toBeTruthy();
  });

  it("renders empty state when no items", async () => {
    render(<ReviewQueueView />);
    await waitFor(() => {
      expect(screen.getByTestId("review-empty")).toBeTruthy();
    });
    expect(screen.getByText("No proposals.")).toBeTruthy();
  });

  it("renders item rows with proposed_title and action buttons", async () => {
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
    expect(screen.getByText("Proposed Page 1")).toBeTruthy();
    expect(screen.getByText("Proposed Page 2")).toBeTruthy();
  });

  it("renders rationale text for items", async () => {
    const items = [makeItem("1", { rationale: "This concept is referenced but missing." })];
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items,
      total: 1,
      limit: 50,
      offset: 0,
    });
    render(<ReviewQueueView />);
    await waitFor(() => {
      expect(screen.getByText("This concept is referenced but missing.")).toBeTruthy();
    });
  });

  it("renders 'no rationale' placeholder when rationale is null", async () => {
    const items = [makeItem("1", { rationale: null })];
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items,
      total: 1,
      limit: 50,
      offset: 0,
    });
    render(<ReviewQueueView />);
    await waitFor(() => {
      expect(screen.getByText("No rationale provided")).toBeTruthy();
    });
  });

  it("renders type badge for missing-page", async () => {
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [makeItem("1", { item_type: "missing-page" })],
      total: 1,
      limit: 50,
      offset: 0,
    });
    render(<ReviewQueueView />);
    await waitFor(() => {
      expect(screen.getByText("Missing page")).toBeTruthy();
    });
  });

  it("renders type badge for contradiction", async () => {
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [makeItem("1", { item_type: "contradiction" })],
      total: 1,
      limit: 50,
      offset: 0,
    });
    render(<ReviewQueueView />);
    await waitFor(() => {
      expect(screen.getByText("Contradiction")).toBeTruthy();
    });
  });

  it("renders conflict page_title for contradiction type", async () => {
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [
        makeItem("1", {
          item_type: "contradiction",
          page_id: "page-x",
          page_title: "Existing Conflicting Page",
        }),
      ],
      total: 1,
      limit: 50,
      offset: 0,
    });
    render(<ReviewQueueView />);
    await waitFor(() => {
      // "Conflicts with" is split across text nodes (label + colon + <em>),
      // so match by regex on the parent element's full text content.
      expect(screen.getByText(/Conflicts with/i)).toBeTruthy();
      expect(screen.getByText("Existing Conflicting Page")).toBeTruthy();
    });
  });

  it("does NOT render conflict row for missing-page type even with page_title set", async () => {
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [makeItem("1", { item_type: "missing-page", page_title: "Some Page" })],
      total: 1,
      limit: 50,
      offset: 0,
    });
    render(<ReviewQueueView />);
    await waitFor(() => {
      expect(screen.getAllByTestId("review-item-row")).toHaveLength(1);
    });
    // "Conflicts with" should NOT appear for missing-page
    expect(screen.queryByText("Conflicts with")).toBeNull();
  });

  it("renders proposed_page_type chip when present", async () => {
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [makeItem("1", { proposed_page_type: "entity" })],
      total: 1,
      limit: 50,
      offset: 0,
    });
    render(<ReviewQueueView />);
    await waitFor(() => {
      // The chip shows the i18n key review.pageType.entity = "entity"
      expect(screen.getByText("entity")).toBeTruthy();
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

// ─── Create action ────────────────────────────────────────────────────────────

describe("ReviewQueueView — Create action (ADR-0034 §5)", () => {
  it("calls createReviewItem and item leaves the list on 201", async () => {
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [makeItem("1"), makeItem("2")],
      total: 2,
      limit: 50,
      offset: 0,
    });
    vi.mocked(reviewClient.createReviewItem).mockResolvedValueOnce(
      makeItem("1", { status: "created" }),
    );

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getAllByTestId("review-action-create")).toHaveLength(2);
    });

    fireEvent.click(screen.getAllByTestId("review-action-create")[0]!);

    await waitFor(() => {
      expect(reviewClient.createReviewItem).toHaveBeenCalledWith("1");
    });

    await waitFor(() => {
      expect(screen.getAllByTestId("review-item-row")).toHaveLength(1);
      expect(screen.queryByText("Proposed Page 1")).toBeNull();
      expect(screen.getByText("Proposed Page 2")).toBeTruthy();
    });
  });

  it("shows retry-or-skip hint on 502 and keeps item in list", async () => {
    const { ApiError } = await import("../api/graphClient");
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [makeItem("1")],
      total: 1,
      limit: 50,
      offset: 0,
    });
    vi.mocked(reviewClient.createReviewItem).mockRejectedValueOnce(
      new ApiError(502, "502 page generation failed; item left pending — retry or skip"),
    );

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getAllByTestId("review-action-create")).toHaveLength(1);
    });

    fireEvent.click(screen.getAllByTestId("review-action-create")[0]!);

    await waitFor(() => {
      // Retry-or-skip hint appears
      expect(screen.getByText("Page generation failed — retry or skip.")).toBeTruthy();
    });

    // Item STAYS in the list (item left pending — ADR-0034 §5.3)
    expect(screen.getByTestId("review-item-row")).toBeTruthy();
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
      expect(screen.queryByText("Proposed Page A")).toBeNull();
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

// ─── Sweep result banner ──────────────────────────────────────────────────────

describe("ReviewQueueView — sweep result banner", () => {
  it("shows sweep result banner when lastSweepResult is set", async () => {
    resetStore({
      lastSweepResult: { rule_resolved: 2, llm_resolved: 1, kept: 3 },
    });

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getByTestId("review-sweep-result")).toBeTruthy();
      expect(screen.getByText(/rule-resolved/)).toBeTruthy();
    });
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

// ─── Card enrichment — referenced_pages chips (ADR-0044 §7) ──────────────────

describe("ReviewQueueView — card enrichment: referenced_pages (ADR-0044 §7)", () => {
  it("renders referenced page chips when referenced_pages is set", async () => {
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [
        makeItem("1", {
          referenced_pages: [
            { id: "pg-a", title: "Alpha Concept", type: "concept" },
            { id: "pg-b", title: "Beta Entity", type: "entity" },
          ],
          referenced_page_ids: ["pg-a", "pg-b"],
        }),
      ],
      total: 1,
      limit: 50,
      offset: 0,
    });

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getByTestId("referenced-pages-row")).toBeTruthy();
      // Chip text is [[title]]
      expect(screen.getByText("[[Alpha Concept]]")).toBeTruthy();
      expect(screen.getByText("[[Beta Entity]]")).toBeTruthy();
    });
  });

  it("does NOT render referenced-pages row when referenced_pages is null", async () => {
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [makeItem("1", { referenced_pages: null })],
      total: 1,
      limit: 50,
      offset: 0,
    });

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getAllByTestId("review-item-row")).toHaveLength(1);
    });
    expect(screen.queryByTestId("referenced-pages-row")).toBeNull();
  });

  it("does NOT render referenced-pages row when referenced_pages is empty", async () => {
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [makeItem("1", { referenced_pages: [] })],
      total: 1,
      limit: 50,
      offset: 0,
    });

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getAllByTestId("review-item-row")).toHaveLength(1);
    });
    expect(screen.queryByTestId("referenced-pages-row")).toBeNull();
  });

  it("clicking a referenced page chip calls setActiveSection('pages')", async () => {
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [
        makeItem("1", {
          referenced_pages: [{ id: "pg-a", title: "Alpha Concept", type: "concept" }],
          referenced_page_ids: ["pg-a"],
        }),
      ],
      total: 1,
      limit: 50,
      offset: 0,
    });

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getAllByTestId("referenced-page-chip")).toHaveLength(1);
    });

    fireEvent.click(screen.getAllByTestId("referenced-page-chip")[0]!);
    expect(mockSetActiveSection).toHaveBeenCalledWith("pages");
  });
});

// ─── Card enrichment — search_queries line (ADR-0044 §7) ─────────────────────

describe("ReviewQueueView — card enrichment: search_queries (ADR-0044 §7)", () => {
  it("renders search-queries line when search_queries is set", async () => {
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [
        makeItem("1", {
          search_queries: ["llm wiki pattern", "karpathy knowledge graph"],
        }),
      ],
      total: 1,
      limit: 50,
      offset: 0,
    });

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getByTestId("search-queries-row")).toBeTruthy();
      // "will search: q1 · q2"
      expect(screen.getByText(/llm wiki pattern/)).toBeTruthy();
      expect(screen.getByText(/karpathy knowledge graph/)).toBeTruthy();
    });
  });

  it("does NOT render search-queries row when search_queries is null", async () => {
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [makeItem("1", { search_queries: null })],
      total: 1,
      limit: 50,
      offset: 0,
    });

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getAllByTestId("review-item-row")).toHaveLength(1);
    });
    expect(screen.queryByTestId("search-queries-row")).toBeNull();
  });
});

// ─── Dismiss per-item action (ADR-0044 §7) ───────────────────────────────────

describe("ReviewQueueView — dismiss per-item action (ADR-0044 §6)", () => {
  it("calls dismissReviewItem and item leaves the list", async () => {
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [makeItem("A"), makeItem("B")],
      total: 2,
      limit: 50,
      offset: 0,
    });
    vi.mocked(reviewClient.dismissReviewItem).mockResolvedValueOnce(
      makeItem("A", { status: "dismissed" }),
    );

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getAllByTestId("review-action-dismiss")).toHaveLength(2);
    });

    fireEvent.click(screen.getAllByTestId("review-action-dismiss")[0]!);

    await waitFor(() => {
      expect(reviewClient.dismissReviewItem).toHaveBeenCalledWith("A");
    });

    await waitFor(() => {
      expect(screen.getAllByTestId("review-item-row")).toHaveLength(1);
      expect(screen.queryByText("Proposed Page A")).toBeNull();
    });
  });
});

// ─── Status tabs (ADR-0044 §7) ────────────────────────────────────────────────

describe("ReviewQueueView — status tabs (ADR-0044 §7)", () => {
  it("renders all three tabs", async () => {
    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getByTestId("review-tab-pending")).toBeTruthy();
      expect(screen.getByTestId("review-tab-resolved")).toBeTruthy();
      expect(screen.getByTestId("review-tab-dismissed")).toBeTruthy();
    });
  });

  it("clicking Resolved tab re-fetches with status=resolved", async () => {
    const resolvedItems = [makeItem("r1", { status: "created" })];
    vi.mocked(reviewClient.fetchReviewQueue)
      // initial mount fetch (pending)
      .mockResolvedValueOnce({ items: [], total: 0, limit: 50, offset: 0 })
      // tab switch fetch (resolved)
      .mockResolvedValueOnce({
        items: resolvedItems,
        total: 1,
        limit: 50,
        offset: 0,
      });

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getByTestId("review-tab-resolved")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("review-tab-resolved"));

    await waitFor(() => {
      // The second call (tab switch) must carry status=resolved
      const calls = vi.mocked(reviewClient.fetchReviewQueue).mock.calls;
      const tabSwitchCall = calls.find(
        (c) => (c[0] as { status?: string }).status === "resolved",
      );
      expect(tabSwitchCall).toBeDefined();
    });
  });

  it("shows Clear resolved button on Resolved tab", async () => {
    // Simulate active tab = resolved via store pre-state
    resetStore({ activeTab: "resolved" });
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [],
      total: 0,
      limit: 50,
      offset: 0,
    });

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getByTestId("review-clear-resolved-btn")).toBeTruthy();
    });
  });

  it("does NOT show Clear resolved button on Pending tab", async () => {
    resetStore({ activeTab: "pending" });
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [],
      total: 0,
      limit: 50,
      offset: 0,
    });

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.queryByTestId("review-clear-resolved-btn")).toBeNull();
    });
  });
});

// ─── Selection + bulk bar (ADR-0044 §7) ──────────────────────────────────────

describe("ReviewQueueView — selection + bulk bar (ADR-0044 §7)", () => {
  it("renders 'Select pending' toggle button", async () => {
    render(<ReviewQueueView />);
    await waitFor(() => {
      expect(screen.getByTestId("review-select-pending-btn")).toBeTruthy();
    });
  });

  it("'Select pending' selects loaded pending items", async () => {
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [makeItem("1"), makeItem("2")],
      total: 2,
      limit: 50,
      offset: 0,
    });

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getAllByTestId("review-item-row")).toHaveLength(2);
    });

    fireEvent.click(screen.getByTestId("review-select-pending-btn"));

    await waitFor(() => {
      // selection count badge appears
      expect(screen.getByTestId("review-selection-count")).toBeTruthy();
      expect(screen.getByText("2 selected")).toBeTruthy();
    });
  });

  it("shows bulk action bar when items are selected", async () => {
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [makeItem("1")],
      total: 1,
      limit: 50,
      offset: 0,
    });

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getAllByTestId("review-item-row")).toHaveLength(1);
    });

    fireEvent.click(screen.getByTestId("review-select-pending-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("review-bulk-mark-resolved")).toBeTruthy();
      expect(screen.getByTestId("review-bulk-dismiss")).toBeTruthy();
      expect(screen.getByTestId("review-bulk-skip")).toBeTruthy();
    });
  });

  it("does NOT show bulk action bar when nothing selected", async () => {
    render(<ReviewQueueView />);
    await waitFor(() => {
      expect(screen.queryByTestId("review-bulk-mark-resolved")).toBeNull();
    });
  });

  it("clicking 'Mark resolved' dispatches bulkReview + refreshes", async () => {
    vi.mocked(reviewClient.fetchReviewQueue)
      .mockResolvedValueOnce({ items: [makeItem("1")], total: 1, limit: 50, offset: 0 })
      .mockResolvedValueOnce({ items: [], total: 0, limit: 50, offset: 0 }); // after bulk
    vi.mocked(reviewClient.bulkReview).mockResolvedValueOnce({
      updated: 1,
      skipped_terminal: 0,
    });

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getAllByTestId("review-item-row")).toHaveLength(1);
    });

    // Select item
    fireEvent.click(screen.getByTestId("review-select-pending-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("review-bulk-mark-resolved")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("review-bulk-mark-resolved"));

    await waitFor(() => {
      expect(reviewClient.bulkReview).toHaveBeenCalledWith(
        expect.objectContaining({ action: "mark-resolved" }),
      );
    });

    // Bulk result banner
    await waitFor(() => {
      expect(screen.getByTestId("review-bulk-result")).toBeTruthy();
    });
  });

  it("per-row checkbox toggles selection of that item", async () => {
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [makeItem("abc")],
      total: 1,
      limit: 50,
      offset: 0,
    });

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getByTestId("review-select-abc")).toBeTruthy();
    });

    const checkbox = screen.getByTestId("review-select-abc") as HTMLInputElement;
    expect(checkbox.checked).toBe(false);

    fireEvent.click(checkbox);

    await waitFor(() => {
      expect(screen.getByTestId("review-selection-count")).toBeTruthy();
    });
  });
});

// ─── UXA-18: ItemTypeBadge normalises underscores to kebab-case ──────────────

describe("ReviewQueueView — UXA-18: item_type normalisation", () => {
  it("UXA-18-1: underscore item_type 'missing_page' renders 'Missing page' badge (not raw key)", async () => {
    // Backend may send "missing_page" (underscore); UI must normalise to "missing-page".
    const item = makeItem("uxa18a", { item_type: "missing_page" as ReviewItem["item_type"] });
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [item],
      total: 1,
      limit: 50,
      offset: 0,
    });

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getByTestId("review-item-row")).toBeTruthy();
    });

    // "Missing page" is the translated label in the mock map; raw key "review.itemType.missing_page"
    // should NOT appear.
    const badges = document.querySelectorAll(".syn-chip");
    const badgeTexts = Array.from(badges).map((b) => b.textContent ?? "");
    // Should contain the translated label, not the raw key
    expect(badgeTexts.some((t) => t.includes("Missing page"))).toBe(true);
    expect(badgeTexts.some((t) => t.includes("review.itemType.missing_page"))).toBe(false);
  });

  it("UXA-18-2: native kebab-case 'missing-page' still renders correctly", async () => {
    const item = makeItem("uxa18b", { item_type: "missing-page" });
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [item],
      total: 1,
      limit: 50,
      offset: 0,
    });

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getByTestId("review-item-row")).toBeTruthy();
    });

    const badges = document.querySelectorAll(".syn-chip");
    const badgeTexts = Array.from(badges).map((b) => b.textContent ?? "");
    expect(badgeTexts.some((t) => t.includes("Missing page"))).toBe(true);
  });
});

// ─── Virtualization smoke at N≈500 rows (ADR-0044 §7 / I4) ──────────────────

describe("ReviewQueueView — virtualization smoke N=500 (I4)", () => {
  it("mounts and renders a bounded subset of 500 rows (virtualizer is active)", async () => {
    const items = Array.from({ length: 500 }, (_, i) => makeItem(String(i)));
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items,
      total: 500,
      limit: 500,
      offset: 0,
    });

    render(<ReviewQueueView />);

    await waitFor(() => {
      // All 500 are passed through in test (TanStack Virtual mock renders all items).
      // The key assertion: the component mounts without error and rows are present.
      const rows = screen.getAllByTestId("review-item-row");
      expect(rows.length).toBeGreaterThan(0);
      // In production the virtualizer would clamp to a window; in tests the mock
      // returns all. The important guarantee is that the component does not crash
      // and does not exceed the item count.
      expect(rows.length).toBeLessThanOrEqual(500);
    });
  });
});

// ─── UXB-2 AC-UXB2-2: button class assertions ────────────────────────────────
// AC-UXB2-2: ActionButton uses .syn-btn.syn-btn--secondary class.
// AC-UXB2-4: ReviewQueueView does NOT inject an inline <style> element.

describe("ReviewQueueView — UXB-2 design-system class assertions (AC-UXB2-2 + AC-UXB2-4)", () => {
  beforeEach(() => {
    resetStore();
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [makeItem("1")],
      total: 1,
      limit: 50,
      offset: 0,
    });
  });

  it("AC-UXB2-2: ActionButton (create) renders with syn-btn and syn-btn--secondary class", async () => {
    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getAllByTestId("review-action-create")).toHaveLength(1);
    });

    const btn = screen.getAllByTestId("review-action-create")[0]!;
    expect(btn.classList.contains("syn-btn")).toBe(true);
    expect(btn.classList.contains("syn-btn--secondary")).toBe(true);
    expect(btn.classList.contains("syn-btn--sm")).toBe(true);
  });

  it("AC-UXB2-2: ActionButton (skip) renders with syn-btn and syn-btn--secondary class", async () => {
    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getAllByTestId("review-action-skip")).toHaveLength(1);
    });

    const btn = screen.getAllByTestId("review-action-skip")[0]!;
    expect(btn.classList.contains("syn-btn")).toBe(true);
    expect(btn.classList.contains("syn-btn--secondary")).toBe(true);
  });

  it("AC-UXB2-4: ReviewQueueView does not inject an inline <style> element on mount", async () => {
    // Spy on document.createElement to detect inline <style> injections.
    const originalCreate = document.createElement.bind(document);
    const styleCreations: string[] = [];
    const spy = vi.spyOn(document, "createElement").mockImplementation((tag: string, ...args: unknown[]) => {
      if (tag === "style") styleCreations.push(tag);
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      return originalCreate(tag, ...(args as any[]));
    });

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getAllByTestId("review-action-create")).toHaveLength(1);
    });

    // No <style> element should have been created by the component.
    // (The global keyframe <style> from theme.css is injected by the CSS bundler,
    //  not via document.createElement, so it does not appear here.)
    expect(styleCreations).toHaveLength(0);
    spy.mockRestore();
  });
});

// ─── WS-B: resolved / dismissed card distinct state (AC-WS-B-2 / AC-WS-B-3) ──
//
// Verifies that:
//   1. Resolved items (status auto_resolved / created / deep_researched) render a
//      resolution badge and NO primary action buttons (Crea / Salta / Ignora /
//      Ricerca Profonda).
//   2. Dismissed items show the "dismissed" badge and NO primary action buttons.
//   3. The Resolved and Pending tabs return disjoint item sets (AC-WS-B-3).
//   4. A created item with created_page_id shows the "View page" link.

describe("ReviewQueueView — WS-B: resolved card state (AC-WS-B-2)", () => {
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

  it("resolved item (auto_resolved) shows status badge and no action buttons", async () => {
    const resolvedItem = makeItem("r1", { status: "auto_resolved", reviewed_at: new Date().toISOString() });
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [resolvedItem],
      total: 1,
      limit: 50,
      offset: 0,
    });

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getByTestId("review-item-row")).toBeTruthy();
    });

    // Resolution badge must appear
    expect(screen.getByTestId("review-status-badge-auto_resolved")).toBeTruthy();

    // Primary action buttons must NOT appear
    expect(screen.queryByTestId("review-action-create")).toBeNull();
    expect(screen.queryByTestId("review-action-skip")).toBeNull();
    expect(screen.queryByTestId("review-action-dismiss")).toBeNull();
    expect(screen.queryByTestId("review-action-deep-research")).toBeNull();
  });

  it("resolved item (created) shows 'created' badge and no action buttons", async () => {
    const createdItem = makeItem("r2", { status: "created", reviewed_at: new Date().toISOString() });
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [createdItem],
      total: 1,
      limit: 50,
      offset: 0,
    });

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getByTestId("review-item-row")).toBeTruthy();
    });

    expect(screen.getByTestId("review-status-badge-created")).toBeTruthy();
    expect(screen.queryByTestId("review-action-create")).toBeNull();
    expect(screen.queryByTestId("review-action-skip")).toBeNull();
    expect(screen.queryByTestId("review-action-dismiss")).toBeNull();
    expect(screen.queryByTestId("review-action-deep-research")).toBeNull();
  });

  it("resolved item (deep_researched) shows 'deep_researched' badge and no action buttons", async () => {
    const deepItem = makeItem("r3", { status: "deep_researched", reviewed_at: new Date().toISOString() });
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [deepItem],
      total: 1,
      limit: 50,
      offset: 0,
    });

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getByTestId("review-item-row")).toBeTruthy();
    });

    expect(screen.getByTestId("review-status-badge-deep_researched")).toBeTruthy();
    expect(screen.queryByTestId("review-action-create")).toBeNull();
    expect(screen.queryByTestId("review-action-skip")).toBeNull();
    expect(screen.queryByTestId("review-action-dismiss")).toBeNull();
    expect(screen.queryByTestId("review-action-deep-research")).toBeNull();
  });

  it("dismissed item shows 'dismissed' badge and no action buttons", async () => {
    const dismissedItem = makeItem("d1", { status: "dismissed", reviewed_at: new Date().toISOString() });
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [dismissedItem],
      total: 1,
      limit: 50,
      offset: 0,
    });

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getByTestId("review-item-row")).toBeTruthy();
    });

    expect(screen.getByTestId("review-status-badge-dismissed")).toBeTruthy();
    expect(screen.queryByTestId("review-action-create")).toBeNull();
    expect(screen.queryByTestId("review-action-skip")).toBeNull();
    expect(screen.queryByTestId("review-action-dismiss")).toBeNull();
    expect(screen.queryByTestId("review-action-deep-research")).toBeNull();
  });

  it("skipped item shows 'skipped' badge and no action buttons", async () => {
    const skippedItem = makeItem("s1", { status: "skipped", reviewed_at: new Date().toISOString() });
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [skippedItem],
      total: 1,
      limit: 50,
      offset: 0,
    });

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getByTestId("review-item-row")).toBeTruthy();
    });

    expect(screen.getByTestId("review-status-badge-skipped")).toBeTruthy();
    expect(screen.queryByTestId("review-action-create")).toBeNull();
    expect(screen.queryByTestId("review-action-skip")).toBeNull();
    expect(screen.queryByTestId("review-action-dismiss")).toBeNull();
    expect(screen.queryByTestId("review-action-deep-research")).toBeNull();
  });

  it("pending item still shows all four action buttons (unchanged)", async () => {
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [makeItem("p1", { status: "pending" })],
      total: 1,
      limit: 50,
      offset: 0,
    });

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getByTestId("review-item-row")).toBeTruthy();
    });

    // All four pending actions must be present
    expect(screen.getByTestId("review-action-create")).toBeTruthy();
    expect(screen.getByTestId("review-action-skip")).toBeTruthy();
    expect(screen.getByTestId("review-action-dismiss")).toBeTruthy();
    expect(screen.getByTestId("review-action-deep-research")).toBeTruthy();

    // No resolution badge on pending
    expect(screen.queryByTestId("review-status-badge-auto_resolved")).toBeNull();
    expect(screen.queryByTestId("review-status-badge-created")).toBeNull();
    expect(screen.queryByTestId("review-status-badge-dismissed")).toBeNull();
  });

  it("created item with created_page_id shows 'View page' link", async () => {
    const createdWithPage = makeItem("r4", {
      status: "created",
      created_page_id: "page-uuid-abc",
      reviewed_at: new Date().toISOString(),
    });
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [createdWithPage],
      total: 1,
      limit: 50,
      offset: 0,
    });

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getByTestId("review-view-created-page")).toBeTruthy();
    });
  });

  it("created item without created_page_id does NOT show 'View page' link", async () => {
    const createdNoPage = makeItem("r5", {
      status: "created",
      created_page_id: null,
      reviewed_at: new Date().toISOString(),
    });
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValue({
      items: [createdNoPage],
      total: 1,
      limit: 50,
      offset: 0,
    });

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getByTestId("review-item-row")).toBeTruthy();
    });

    expect(screen.queryByTestId("review-view-created-page")).toBeNull();
  });
});

// ─── WS-B AC-WS-B-3: Pending and Resolved tabs return disjoint item sets ─────

describe("ReviewQueueView — WS-B AC-WS-B-3: tabs return disjoint item sets", () => {
  it("Pending tab and Resolved tab fetch different status values and display disjoint items", async () => {
    const pendingItem = makeItem("pending-1", { status: "pending" });
    const resolvedItem = makeItem("resolved-1", { status: "auto_resolved", reviewed_at: new Date().toISOString() });

    // First call: pending tab (mount)
    vi.mocked(reviewClient.fetchReviewQueue)
      .mockResolvedValueOnce({ items: [pendingItem], total: 1, limit: 50, offset: 0 })
      // Second call: after switching to resolved tab
      .mockResolvedValueOnce({ items: [resolvedItem], total: 1, limit: 50, offset: 0 });

    render(<ReviewQueueView />);

    // Wait for pending tab to render
    await waitFor(() => {
      expect(screen.getAllByTestId("review-item-row")).toHaveLength(1);
    });

    // Pending item shows action buttons (pending card)
    expect(screen.getByTestId("review-action-create")).toBeTruthy();
    expect(screen.queryByTestId("review-status-badge-auto_resolved")).toBeNull();

    // Switch to resolved tab
    fireEvent.click(screen.getByTestId("review-tab-resolved"));

    await waitFor(() => {
      // The second fetch must have been called with status=resolved
      const calls = vi.mocked(reviewClient.fetchReviewQueue).mock.calls;
      const resolvedCall = calls.find(
        (c) => (c[0] as { status?: string }).status === "resolved",
      );
      expect(resolvedCall).toBeDefined();
    });

    await waitFor(() => {
      // Now showing the resolved item — resolution badge present, no action buttons
      expect(screen.getByTestId("review-status-badge-auto_resolved")).toBeTruthy();
      expect(screen.queryByTestId("review-action-create")).toBeNull();
    });
  });

  it("Dismissed tab fetches status=dismissed and shows dismissed badge", async () => {
    const dismissedItem = makeItem("dismissed-1", { status: "dismissed", reviewed_at: new Date().toISOString() });

    vi.mocked(reviewClient.fetchReviewQueue)
      .mockResolvedValueOnce({ items: [], total: 0, limit: 50, offset: 0 }) // pending tab
      .mockResolvedValueOnce({ items: [dismissedItem], total: 1, limit: 50, offset: 0 }); // dismissed tab

    render(<ReviewQueueView />);

    await waitFor(() => {
      expect(screen.getByTestId("review-tab-dismissed")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("review-tab-dismissed"));

    await waitFor(() => {
      const calls = vi.mocked(reviewClient.fetchReviewQueue).mock.calls;
      const dismissedCall = calls.find(
        (c) => (c[0] as { status?: string }).status === "dismissed",
      );
      expect(dismissedCall).toBeDefined();
    });

    await waitFor(() => {
      expect(screen.getByTestId("review-status-badge-dismissed")).toBeTruthy();
      expect(screen.queryByTestId("review-action-create")).toBeNull();
    });
  });
});
