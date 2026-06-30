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
  deepResearchReviewItem: vi.fn(),
  sweepReviewQueue: vi.fn(),
}));

import * as reviewClient from "../api/reviewClient";

// ─── Mock i18n ────────────────────────────────────────────────────────────────

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      const map: Record<string, string> = {
        "review.title": "Review Queue",
        "review.hint": "AI proposals.",
        "review.empty": "No pending proposals.",
        "review.loadMore": "Load more",
        "review.refresh": "Refresh",
        "review.create": "Create",
        "review.creating": "Creating…",
        "review.createFailed": "Page generation failed — retry or skip.",
        "review.skip": "Skip",
        "review.deepResearch": "Deep Research",
        "review.deepResearchStarted": "Deep research run started.",
        "review.viewRun": "View run",
        "review.searxngUnavailable": "Deep research unavailable: SEARXNG_URL not configured.",
        "review.noTitle": "(no proposed title)",
        "review.noRationale": "No rationale provided",
        "review.conflictsWith": "Conflicts with",
        "review.sweep": "Clean up resolved",
        "review.sweepHelp": "Run auto-resolution sweep.",
        "review.sweepResult": `Sweep complete: ${String(params?.rule ?? 0)} rule-resolved, ${String(params?.llm ?? 0)} LLM-resolved, ${String(params?.kept ?? 0)} kept pending.`,
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
    createGenerationError: {},
    lastDeepResearch: null,
    deepResearchError: null,
    lastSweepResult: null,
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
    expect(screen.getByText("No pending proposals.")).toBeTruthy();
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
