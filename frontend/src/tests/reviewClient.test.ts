/**
 * reviewClient.test.ts — unit tests for the F9 review API client.
 *
 * Covers:
 *   - fetchReviewQueue: builds correct URL with vaultId, limit, offset
 *   - approveReviewItem: POST to correct endpoint
 *   - skipReviewItem: POST to correct endpoint
 *   - deepResearchReviewItem: POST to correct endpoint, returns 202 body
 *   - Error handling: non-ok response throws ApiError with status
 *
 * Mocks global fetch via vi.stubGlobal.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  fetchReviewQueue,
  approveReviewItem,
  skipReviewItem,
  deepResearchReviewItem,
} from "../api/reviewClient";
import type { ReviewItem, ReviewQueueResponse, ReviewDeepResearchResponse } from "../api/types";
import { ApiError } from "../api/graphClient";

// ─── Helpers ──────────────────────────────────────────────────────────────────

function makeItem(id: string): ReviewItem {
  return {
    id,
    vault_id: "default",
    page_id: `page-${id}`,
    page_title: `Page ${id}`,
    item_type: "new_page",
    status: "pending",
    pre_generated_query: "Why?",
    deep_research_run_id: null,
    created_at: new Date().toISOString(),
    reviewed_at: null,
  };
}

function mockFetch(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? "OK" : "Error",
    json: () => Promise.resolve(body),
  });
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

// ─── fetchReviewQueue ─────────────────────────────────────────────────────────

describe("reviewClient — fetchReviewQueue", () => {
  it("calls GET /review/queue with vault_id, limit, offset", async () => {
    const response: ReviewQueueResponse = {
      items: [makeItem("1")],
      total: 1,
      limit: 50,
      offset: 0,
    };
    const fetchMock = mockFetch(response);
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchReviewQueue({ vaultId: "default", limit: 50, offset: 0 });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const url = fetchMock.mock.calls[0]?.[0] as string;
    expect(url).toContain("/review/queue");
    expect(url).toContain("vault_id=default");
    expect(url).toContain("limit=50");
    expect(url).toContain("offset=0");
    expect(result.items).toHaveLength(1);
    expect(result.total).toBe(1);
  });

  it("uses default limit 50 and offset 0", async () => {
    const fetchMock = mockFetch({ items: [], total: 0, limit: 50, offset: 0 });
    vi.stubGlobal("fetch", fetchMock);

    await fetchReviewQueue({ vaultId: "test" });

    const url = fetchMock.mock.calls[0]?.[0] as string;
    expect(url).toContain("limit=50");
    expect(url).toContain("offset=0");
  });

  it("throws ApiError on non-ok response", async () => {
    const fetchMock = mockFetch({ detail: "Validation error" }, 422);
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchReviewQueue({ vaultId: "default" })).rejects.toBeInstanceOf(ApiError);
  });
});

// ─── approveReviewItem ────────────────────────────────────────────────────────

describe("reviewClient — approveReviewItem", () => {
  it("POSTs to /review/queue/{id}/approve", async () => {
    const item = makeItem("abc");
    const fetchMock = mockFetch(item);
    vi.stubGlobal("fetch", fetchMock);

    const result = await approveReviewItem("abc");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, { method: string }];
    expect(url).toContain("/review/queue/abc/approve");
    expect(init.method).toBe("POST");
    expect(result.id).toBe("abc");
  });

  it("throws ApiError on 404", async () => {
    const fetchMock = mockFetch({ detail: "Review item not found" }, 404);
    vi.stubGlobal("fetch", fetchMock);

    await expect(approveReviewItem("missing")).rejects.toBeInstanceOf(ApiError);
    await expect(approveReviewItem("missing")).rejects.toMatchObject({ status: 404 });
  });
});

// ─── skipReviewItem ───────────────────────────────────────────────────────────

describe("reviewClient — skipReviewItem", () => {
  it("POSTs to /review/queue/{id}/skip", async () => {
    const item = makeItem("xyz");
    const fetchMock = mockFetch(item);
    vi.stubGlobal("fetch", fetchMock);

    const result = await skipReviewItem("xyz");

    const [url, init] = fetchMock.mock.calls[0] as [string, { method: string }];
    expect(url).toContain("/review/queue/xyz/skip");
    expect(init.method).toBe("POST");
    expect(result.id).toBe("xyz");
  });
});

// ─── deepResearchReviewItem ───────────────────────────────────────────────────

describe("reviewClient — deepResearchReviewItem", () => {
  it("POSTs to /review/queue/{id}/deep-research and returns run_id", async () => {
    const drResp: ReviewDeepResearchResponse = {
      review_item_id: "item-1",
      run_id: "run-9999",
    };
    const fetchMock = mockFetch(drResp, 202);
    vi.stubGlobal("fetch", fetchMock);

    const result = await deepResearchReviewItem("item-1");

    const [url, init] = fetchMock.mock.calls[0] as [string, { method: string }];
    expect(url).toContain("/review/queue/item-1/deep-research");
    expect(init.method).toBe("POST");
    expect(result.run_id).toBe("run-9999");
    expect(result.review_item_id).toBe("item-1");
  });

  it("throws ApiError with status 503 when SEARXNG_URL not configured", async () => {
    const fetchMock = mockFetch(
      { detail: "SEARXNG_URL is not configured" },
      503,
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(deepResearchReviewItem("item-1")).rejects.toBeInstanceOf(ApiError);
    await expect(deepResearchReviewItem("item-1")).rejects.toMatchObject({ status: 503 });
  });
});
