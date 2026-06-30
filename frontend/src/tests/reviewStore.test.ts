/**
 * reviewStore.test.ts — Zustand store unit tests for F9 HITL review queue (ADR-0034).
 *
 * Covers:
 *   - fetchFresh: items loaded, error state
 *   - create: item removed from pending list on 201 success
 *   - create: 409 (not pending / no provider) → actionError set, item stays pending
 *   - create: 502 (generation failed) → createGenerationError set, item stays pending
 *   - skip: item removed from pending list on success
 *   - deepResearch: item removed + lastDeepResearch set on success
 *   - deepResearch: 503 → deepResearchError set, item NOT removed from list
 *   - sweep: refreshes list after completion
 *   - fetchMore: appends items
 *   - clear helpers
 *
 * All network calls are mocked via vi.mock — no real fetch.
 * INVARIANT I3: store selectors tested independently.
 * INVARIANT I7: create does NOT re-trigger a full ingest scan (AC-F9-6, I1).
 * pre_generated_query is GONE — items now carry proposed_title + rationale (ADR-0034 §7.1).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { useReviewStore } from "../store/reviewStore";
import type { ReviewItem, ReviewQueueResponse, ReviewDeepResearchResponse } from "../api/types";
import { ApiError } from "../api/graphClient";

// ─── Mock API client ──────────────────────────────────────────────────────────

vi.mock("../api/reviewClient", () => ({
  fetchReviewQueue: vi.fn(),
  createReviewItem: vi.fn(),
  skipReviewItem: vi.fn(),
  deepResearchReviewItem: vi.fn(),
  sweepReviewQueue: vi.fn(),
}));

import * as reviewClient from "../api/reviewClient";

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
    rationale: "This page is referenced but does not exist.",
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

function makeQueue(items: ReviewItem[], total?: number): ReviewQueueResponse {
  return { items, total: total ?? items.length, limit: 50, offset: 0 };
}

// ─── Reset store state between tests ─────────────────────────────────────────

beforeEach(() => {
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
  });
  vi.clearAllMocks();
});

// ─── fetchFresh ───────────────────────────────────────────────────────────────

describe("reviewStore — fetchFresh", () => {
  it("loads items on success", async () => {
    const items = [makeItem("1"), makeItem("2")];
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValueOnce(makeQueue(items));

    await useReviewStore.getState().fetchFresh("default");

    const state = useReviewStore.getState();
    expect(state.items).toHaveLength(2);
    expect(state.items[0]?.id).toBe("1");
    expect(state.total).toBe(2);
    expect(state.loading).toBe(false);
    expect(state.error).toBeNull();
  });

  it("sets error on fetch failure", async () => {
    vi.mocked(reviewClient.fetchReviewQueue).mockRejectedValueOnce(
      new Error("Network error"),
    );

    await useReviewStore.getState().fetchFresh("default");

    const state = useReviewStore.getState();
    expect(state.items).toHaveLength(0);
    expect(state.error).toBe("Network error");
    expect(state.loading).toBe(false);
  });

  it("ignores AbortError (navigation away)", async () => {
    const abortErr = Object.assign(new Error("AbortError"), { name: "AbortError" });
    vi.mocked(reviewClient.fetchReviewQueue).mockRejectedValueOnce(abortErr);

    await useReviewStore.getState().fetchFresh("default");

    expect(useReviewStore.getState().error).toBeNull();
  });

  it("replaces existing items on fresh fetch", async () => {
    useReviewStore.setState({ items: [makeItem("old")], total: 1 });

    const newItems = [makeItem("new1"), makeItem("new2")];
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValueOnce(makeQueue(newItems));

    await useReviewStore.getState().fetchFresh("default");

    expect(useReviewStore.getState().items).toHaveLength(2);
    expect(useReviewStore.getState().items[0]?.id).toBe("new1");
  });
});

// ─── create ─────────────────────────────────────────────────────────────────

describe("reviewStore — create (ADR-0034 §5)", () => {
  it("removes the item from the pending list on 201 success", async () => {
    useReviewStore.setState({
      items: [makeItem("1"), makeItem("2")],
      total: 2,
    });
    vi.mocked(reviewClient.createReviewItem).mockResolvedValueOnce(
      makeItem("1", { status: "created", resolution: "created" }),
    );

    await useReviewStore.getState().create("1");

    const state = useReviewStore.getState();
    expect(state.items).toHaveLength(1);
    expect(state.items[0]?.id).toBe("2");
    expect(state.total).toBe(1);
    expect(state.actionInFlight["1"]).toBeNull();
    expect(state.createGenerationError["1"]).toBeFalsy();
  });

  it("sets actionError on 409 (not pending / no provider) and keeps item in list", async () => {
    useReviewStore.setState({ items: [makeItem("1")], total: 1 });
    vi.mocked(reviewClient.createReviewItem).mockRejectedValueOnce(
      new ApiError(409, "409 item is not pending"),
    );

    await useReviewStore.getState().create("1");

    const state = useReviewStore.getState();
    expect(state.items).toHaveLength(1); // not removed
    expect(state.actionError["1"]).toBeTruthy();
    expect(state.createGenerationError["1"]).toBeFalsy(); // 409 is NOT a 502
    expect(state.actionInFlight["1"]).toBeNull();
  });

  it("sets createGenerationError on 502 (generation failed) and keeps item in list", async () => {
    useReviewStore.setState({ items: [makeItem("1")], total: 1 });
    vi.mocked(reviewClient.createReviewItem).mockRejectedValueOnce(
      new ApiError(502, "502 page generation failed; item left pending — retry or skip"),
    );

    await useReviewStore.getState().create("1");

    const state = useReviewStore.getState();
    expect(state.items).toHaveLength(1); // item stays pending (ADR-0034 §5.3)
    expect(state.createGenerationError["1"]).toBeTruthy();
    expect(state.actionError["1"]).toBeFalsy(); // 502 goes to generationError, not actionError
    expect(state.actionInFlight["1"]).toBeNull();
  });

  it("does NOT call ingest endpoint — one bounded provider call server-side (I1)", async () => {
    vi.mocked(reviewClient.createReviewItem).mockResolvedValueOnce(
      makeItem("1", { status: "created" }),
    );
    await useReviewStore.getState().create("1");
    expect(reviewClient.createReviewItem).toHaveBeenCalledTimes(1);
    expect(reviewClient.createReviewItem).toHaveBeenCalledWith("1");
  });
});

// ─── skip ─────────────────────────────────────────────────────────────────────

describe("reviewStore — skip", () => {
  it("removes the item from the pending list on success", async () => {
    useReviewStore.setState({
      items: [makeItem("A"), makeItem("B")],
      total: 2,
    });
    vi.mocked(reviewClient.skipReviewItem).mockResolvedValueOnce(
      makeItem("A", { status: "skipped" }),
    );

    await useReviewStore.getState().skip("A");

    const state = useReviewStore.getState();
    expect(state.items).toHaveLength(1);
    expect(state.items[0]?.id).toBe("B");
    expect(state.total).toBe(1);
  });

  it("sets actionError on failure and keeps item in list", async () => {
    useReviewStore.setState({ items: [makeItem("A")], total: 1 });
    vi.mocked(reviewClient.skipReviewItem).mockRejectedValueOnce(
      new Error("500 Internal Server Error"),
    );

    await useReviewStore.getState().skip("A");

    expect(useReviewStore.getState().items).toHaveLength(1);
    expect(useReviewStore.getState().actionError["A"]).toBeTruthy();
  });
});

// ─── deepResearch ─────────────────────────────────────────────────────────────

describe("reviewStore — deepResearch", () => {
  it("removes item from list + sets lastDeepResearch on success", async () => {
    useReviewStore.setState({
      items: [makeItem("X"), makeItem("Y")],
      total: 2,
    });
    const drResp: ReviewDeepResearchResponse = {
      review_item_id: "X",
      run_id: "run-abc-123",
    };
    vi.mocked(reviewClient.deepResearchReviewItem).mockResolvedValueOnce(drResp);

    const result = await useReviewStore.getState().deepResearch("X");

    const state = useReviewStore.getState();
    expect(result).toEqual(drResp);
    expect(state.items).toHaveLength(1);
    expect(state.items[0]?.id).toBe("Y");
    expect(state.total).toBe(1);
    expect(state.lastDeepResearch).toEqual({ itemId: "X", runId: "run-abc-123" });
    expect(state.deepResearchError).toBeNull();
  });

  it("returns null and sets deepResearchError on 503 (SEARXNG unavailable)", async () => {
    useReviewStore.setState({ items: [makeItem("X")], total: 1 });
    vi.mocked(reviewClient.deepResearchReviewItem).mockRejectedValueOnce(
      new ApiError(503, "503 SEARXNG_URL is not configured"),
    );

    const result = await useReviewStore.getState().deepResearch("X");

    const state = useReviewStore.getState();
    expect(result).toBeNull();
    // Item stays in list on 503 (user may reconfigure SEARXNG and retry)
    expect(state.items).toHaveLength(1);
    expect(state.deepResearchError).toBeTruthy();
    expect(state.actionInFlight["X"]).toBeNull();
  });

  it("sets actionError (not deepResearchError) on non-503 failure", async () => {
    useReviewStore.setState({ items: [makeItem("Z")], total: 1 });
    vi.mocked(reviewClient.deepResearchReviewItem).mockRejectedValueOnce(
      new ApiError(404, "404 Review item not found"),
    );

    const result = await useReviewStore.getState().deepResearch("Z");

    const state = useReviewStore.getState();
    expect(result).toBeNull();
    expect(state.deepResearchError).toBeNull();
    expect(state.actionError["Z"]).toBeTruthy();
  });
});

// ─── sweep ────────────────────────────────────────────────────────────────────

describe("reviewStore — sweep (ADR-0034 §6)", () => {
  it("calls sweep, then refreshes queue and stores result", async () => {
    useReviewStore.setState({ items: [makeItem("X"), makeItem("Y")], total: 2 });

    vi.mocked(reviewClient.sweepReviewQueue).mockResolvedValueOnce({
      rule_resolved: 1,
      llm_resolved: 0,
      kept: 1,
    });
    // After sweep, queue has one fewer item (the resolved one is gone)
    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValueOnce(
      makeQueue([makeItem("Y")], 1),
    );

    await useReviewStore.getState().sweep("default");

    const state = useReviewStore.getState();
    expect(reviewClient.sweepReviewQueue).toHaveBeenCalledWith("default");
    expect(state.items).toHaveLength(1);
    expect(state.items[0]?.id).toBe("Y");
    expect(state.lastSweepResult).toEqual({ rule_resolved: 1, llm_resolved: 0, kept: 1 });
    expect(state.loading).toBe(false);
  });
});

// ─── fetchMore ────────────────────────────────────────────────────────────────

describe("reviewStore — fetchMore", () => {
  it("appends items and increments offset", async () => {
    const initial = [makeItem("1"), makeItem("2")];
    const extra = [makeItem("3"), makeItem("4")];
    useReviewStore.setState({ items: initial, total: 4, offset: 0 });

    vi.mocked(reviewClient.fetchReviewQueue).mockResolvedValueOnce({
      items: extra,
      total: 4,
      limit: 50,
      offset: 50,
    });

    await useReviewStore.getState().fetchMore("default");

    const state = useReviewStore.getState();
    expect(state.items).toHaveLength(4);
    expect(state.offset).toBe(50);
  });

  it("does nothing when all items already loaded", async () => {
    useReviewStore.setState({ items: [makeItem("1")], total: 1, offset: 0 });
    await useReviewStore.getState().fetchMore("default");
    expect(reviewClient.fetchReviewQueue).not.toHaveBeenCalled();
  });
});

// ─── clear helpers ────────────────────────────────────────────────────────────

describe("reviewStore — clear helpers", () => {
  it("clearDeepResearchError clears the error", () => {
    useReviewStore.setState({ deepResearchError: "some error" });
    useReviewStore.getState().clearDeepResearchError();
    expect(useReviewStore.getState().deepResearchError).toBeNull();
  });

  it("clearLastDeepResearch clears the last result", () => {
    useReviewStore.setState({ lastDeepResearch: { itemId: "x", runId: "y" } });
    useReviewStore.getState().clearLastDeepResearch();
    expect(useReviewStore.getState().lastDeepResearch).toBeNull();
  });

  it("clearLastSweepResult clears the sweep result", () => {
    useReviewStore.setState({ lastSweepResult: { rule_resolved: 1, llm_resolved: 0, kept: 2 } });
    useReviewStore.getState().clearLastSweepResult();
    expect(useReviewStore.getState().lastSweepResult).toBeNull();
  });

  it("clearCreateGenerationError clears per-item error", () => {
    useReviewStore.setState({ createGenerationError: { "item-1": "gen failed" } });
    useReviewStore.getState().clearCreateGenerationError("item-1");
    expect(useReviewStore.getState().createGenerationError["item-1"]).toBeFalsy();
  });
});
