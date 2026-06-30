/**
 * reviewClient.test.ts — unit tests for the F9 review API client (ADR-0034 §7).
 *
 * Covers:
 *   - fetchReviewQueue: builds correct URL with vaultId, limit, offset
 *   - createReviewItem: POST to /review/queue/{id}/create (preferred alias, ADR-0034 §7)
 *   - skipReviewItem: POST to correct endpoint
 *   - deepResearchReviewItem: POST to correct endpoint, returns 202 body
 *   - sweepReviewQueue: POST to /review/queue/sweep
 *   - Error handling: non-ok response throws ApiError with status
 *   - 409 / 502 handling for Create (ADR-0034 §5.3)
 *
 * Mocks global fetch via vi.stubGlobal.
 * pre_generated_query is GONE — items now carry proposed_title + rationale.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  fetchReviewQueue,
  createReviewItem,
  skipReviewItem,
  deepResearchReviewItem,
  sweepReviewQueue,
} from "../api/reviewClient";
import type { ReviewItem, ReviewQueueResponse, ReviewDeepResearchResponse } from "../api/types";
import { ApiError } from "../api/graphClient";

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

function mockFetch(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? "OK" : status === 201 ? "Created" : "Error",
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

  it("returned items carry the ADR-0034 §7.1 projection (no pre_generated_query)", async () => {
    const item = makeItem("1");
    const fetchMock = mockFetch({ items: [item], total: 1, limit: 50, offset: 0 });
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchReviewQueue({ vaultId: "default" });
    const first = result.items[0]!;
    expect(first.proposed_title).toBe("Proposed Page 1");
    expect(first.rationale).toBeTruthy();
    expect("pre_generated_query" in first).toBe(false);
  });
});

// ─── createReviewItem ─────────────────────────────────────────────────────────

describe("reviewClient — createReviewItem", () => {
  it("POSTs to /review/queue/{id}/create (preferred alias — ADR-0034 §7)", async () => {
    const item = makeItem("abc", { status: "created" });
    const fetchMock = mockFetch(item, 201);
    vi.stubGlobal("fetch", fetchMock);

    const result = await createReviewItem("abc");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, { method: string }];
    expect(url).toContain("/review/queue/abc/create");
    expect(init.method).toBe("POST");
    expect(result.id).toBe("abc");
    expect(result.status).toBe("created");
  });

  it("throws ApiError with status 409 when item not pending or no provider (ADR-0034 §5.3)", async () => {
    const fetchMock = mockFetch({ detail: "item is not pending" }, 409);
    vi.stubGlobal("fetch", fetchMock);

    await expect(createReviewItem("abc")).rejects.toBeInstanceOf(ApiError);
    await expect(createReviewItem("abc")).rejects.toMatchObject({ status: 409 });
  });

  it("throws ApiError with status 502 when generation fails; item stays pending (ADR-0034 §5.3)", async () => {
    const fetchMock = mockFetch(
      { detail: "page generation failed; item left pending — retry or skip" },
      502,
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(createReviewItem("abc")).rejects.toBeInstanceOf(ApiError);
    await expect(createReviewItem("abc")).rejects.toMatchObject({ status: 502 });
  });

  it("throws ApiError on 404 (item not found)", async () => {
    const fetchMock = mockFetch({ detail: "Review item not found" }, 404);
    vi.stubGlobal("fetch", fetchMock);

    await expect(createReviewItem("missing")).rejects.toBeInstanceOf(ApiError);
    await expect(createReviewItem("missing")).rejects.toMatchObject({ status: 404 });
  });
});

// ─── skipReviewItem ───────────────────────────────────────────────────────────

describe("reviewClient — skipReviewItem", () => {
  it("POSTs to /review/queue/{id}/skip", async () => {
    const item = makeItem("xyz", { status: "skipped" });
    const fetchMock = mockFetch(item);
    vi.stubGlobal("fetch", fetchMock);

    const result = await skipReviewItem("xyz");

    const [url, init] = fetchMock.mock.calls[0] as [string, { method: string }];
    expect(url).toContain("/review/queue/xyz/skip");
    expect(init.method).toBe("POST");
    expect(result.id).toBe("xyz");
    expect(result.status).toBe("skipped");
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

// ─── sweepReviewQueue ─────────────────────────────────────────────────────────

describe("reviewClient — sweepReviewQueue", () => {
  it("POSTs to /review/queue/sweep with vault_id param", async () => {
    const sweepResp = { rule_resolved: 2, llm_resolved: 0, kept: 3 };
    const fetchMock = mockFetch(sweepResp);
    vi.stubGlobal("fetch", fetchMock);

    const result = await sweepReviewQueue("default");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, { method: string }];
    expect(url).toContain("/review/queue/sweep");
    expect(url).toContain("vault_id=default");
    expect(init.method).toBe("POST");
    expect(result.rule_resolved).toBe(2);
    expect(result.llm_resolved).toBe(0);
    expect(result.kept).toBe(3);
  });
});
