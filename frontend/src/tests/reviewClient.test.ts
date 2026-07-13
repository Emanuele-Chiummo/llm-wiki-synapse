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
  dismissReviewItem,
  resolveReviewItem,
  deepResearchReviewItem,
  bulkReview,
  sweepReviewQueue,
  clearResolved,
} from "../api/reviewClient";
import type {
  ReviewItem,
  ReviewQueueResponse,
  ReviewDeepResearchResponse,
  ReviewBulkResponse,
  ReviewClearResolvedResponse,
} from "../api/types";
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
    content_key: null,
    referenced_page_ids: null,
    referenced_pages: null,
    search_queries: null,
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

  it("serializes v1.6 item type, origin, and proposed page type filters", async () => {
    const fetchMock = mockFetch({ items: [], total: 0, limit: 50, offset: 0 });
    vi.stubGlobal("fetch", fetchMock);

    await fetchReviewQueue({
      vaultId: "default",
      itemType: "suggestion",
      proposalOrigin: "corpus",
      proposedPageType: "comparison",
    });

    const url = new URL(fetchMock.mock.calls[0]?.[0] as string, "http://localhost");
    expect(url.searchParams.get("item_type")).toBe("suggestion");
    expect(url.searchParams.get("proposal_origin")).toBe("corpus");
    expect(url.searchParams.get("proposed_page_type")).toBe("comparison");
  });

  it("omits empty v1.6 filters instead of sending blank query params", async () => {
    const fetchMock = mockFetch({ items: [], total: 0, limit: 50, offset: 0 });
    vi.stubGlobal("fetch", fetchMock);

    await fetchReviewQueue({
      vaultId: "default",
      itemType: null,
      proposalOrigin: null,
      proposedPageType: null,
    });

    const url = new URL(fetchMock.mock.calls[0]?.[0] as string, "http://localhost");
    expect(url.searchParams.has("item_type")).toBe(false);
    expect(url.searchParams.has("proposal_origin")).toBe(false);
    expect(url.searchParams.has("proposed_page_type")).toBe(false);
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

// ─── fetchReviewQueue — status param (ADR-0044) ───────────────────────────────

describe("reviewClient — fetchReviewQueue status param (ADR-0044 §6)", () => {
  it("includes ?status=pending by default", async () => {
    const fetchMock = mockFetch({ items: [], total: 0, limit: 50, offset: 0 });
    vi.stubGlobal("fetch", fetchMock);

    await fetchReviewQueue({ vaultId: "default" });

    const url = fetchMock.mock.calls[0]?.[0] as string;
    expect(url).toContain("status=pending");
  });

  it("passes status=resolved when specified", async () => {
    const fetchMock = mockFetch({ items: [], total: 0, limit: 50, offset: 0 });
    vi.stubGlobal("fetch", fetchMock);

    await fetchReviewQueue({ vaultId: "default", status: "resolved" });

    const url = fetchMock.mock.calls[0]?.[0] as string;
    expect(url).toContain("status=resolved");
  });

  it("passes status=dismissed when specified", async () => {
    const fetchMock = mockFetch({ items: [], total: 0, limit: 50, offset: 0 });
    vi.stubGlobal("fetch", fetchMock);

    await fetchReviewQueue({ vaultId: "default", status: "dismissed" });

    const url = fetchMock.mock.calls[0]?.[0] as string;
    expect(url).toContain("status=dismissed");
  });

  it("returned items carry ADR-0044 §6.1 projection fields", async () => {
    const item = makeItem("1", {
      content_key: "abcd1234abcd1234",
      referenced_page_ids: ["page-a", "page-b"],
      referenced_pages: [
        { id: "page-a", title: "Page A", type: "concept" },
        { id: "page-b", title: "Page B", type: "entity" },
      ],
      search_queries: ["query one", "query two"],
    });
    const fetchMock = mockFetch({ items: [item], total: 1, limit: 50, offset: 0 });
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchReviewQueue({ vaultId: "default" });
    const first = result.items[0]!;
    expect(first.content_key).toBe("abcd1234abcd1234");
    expect(first.referenced_page_ids).toEqual(["page-a", "page-b"]);
    expect(first.referenced_pages).toHaveLength(2);
    expect(first.search_queries).toEqual(["query one", "query two"]);
  });
});

// ─── dismissReviewItem (ADR-0044) ─────────────────────────────────────────────

describe("reviewClient — dismissReviewItem (ADR-0044 §6)", () => {
  it("POSTs to /review/queue/{id}/dismiss", async () => {
    const item = makeItem("abc", { status: "dismissed", resolution: "dismissed" });
    const fetchMock = mockFetch(item);
    vi.stubGlobal("fetch", fetchMock);

    const result = await dismissReviewItem("abc");

    const [url, init] = fetchMock.mock.calls[0] as [string, { method: string }];
    expect(url).toContain("/review/queue/abc/dismiss");
    expect(init.method).toBe("POST");
    expect(result.id).toBe("abc");
    expect(result.status).toBe("dismissed");
  });

  it("throws ApiError on 404", async () => {
    const fetchMock = mockFetch({ detail: "Not found" }, 404);
    vi.stubGlobal("fetch", fetchMock);
    await expect(dismissReviewItem("missing")).rejects.toBeInstanceOf(ApiError);
  });
});

// ─── bulkReview (ADR-0044) ────────────────────────────────────────────────────

describe("reviewClient — bulkReview (ADR-0044 §6)", () => {
  it("POSTs to /review/queue/bulk with JSON body", async () => {
    const bulkResp: ReviewBulkResponse = { updated: 3, skipped_terminal: 1 };
    const fetchMock = mockFetch(bulkResp);
    vi.stubGlobal("fetch", fetchMock);

    const result = await bulkReview({
      vault_id: "default",
      action: "skip",
      ids: ["id1", "id2", "id3"],
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [
      string,
      { method: string; headers: Record<string, string>; body: string },
    ];
    expect(url).toContain("/review/queue/bulk");
    expect(init.method).toBe("POST");
    expect(init.headers["Content-Type"]).toBe("application/json");
    const body = JSON.parse(init.body) as { vault_id: string; action: string; ids: string[] };
    expect(body.vault_id).toBe("default");
    expect(body.action).toBe("skip");
    expect(body.ids).toEqual(["id1", "id2", "id3"]);
    expect(result.updated).toBe(3);
    expect(result.skipped_terminal).toBe(1);
  });

  it("throws ApiError on 400 (ids over cap)", async () => {
    const fetchMock = mockFetch({ detail: "Too many ids" }, 400);
    vi.stubGlobal("fetch", fetchMock);
    await expect(
      bulkReview({ vault_id: "default", action: "dismiss", ids: ["a"] }),
    ).rejects.toBeInstanceOf(ApiError);
    await expect(
      bulkReview({ vault_id: "default", action: "dismiss", ids: ["a"] }),
    ).rejects.toMatchObject({ status: 400 });
  });
});

// ─── resolveReviewItem (R2) ───────────────────────────────────────────────────

describe("reviewClient — resolveReviewItem (R2 Approve action)", () => {
  it("calls POST /review/queue/bulk with action=mark-resolved and single id", async () => {
    const bulkResp: ReviewBulkResponse = { updated: 1, skipped_terminal: 0 };
    const fetchMock = mockFetch(bulkResp);
    vi.stubGlobal("fetch", fetchMock);

    const result = await resolveReviewItem("item-abc", "vault-xyz");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [
      string,
      { method: string; headers: Record<string, string>; body: string },
    ];
    expect(url).toContain("/review/queue/bulk");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body) as { vault_id: string; action: string; ids: string[] };
    expect(body.vault_id).toBe("vault-xyz");
    expect(body.action).toBe("mark-resolved");
    expect(body.ids).toEqual(["item-abc"]);
    expect(result.updated).toBe(1);
  });

  it("throws ApiError on failure", async () => {
    const fetchMock = mockFetch({ detail: "Item not found" }, 404);
    vi.stubGlobal("fetch", fetchMock);
    await expect(resolveReviewItem("missing", "vault")).rejects.toBeInstanceOf(ApiError);
  });
});

// ─── clearResolved (ADR-0044) ─────────────────────────────────────────────────

describe("reviewClient — clearResolved (ADR-0044 §6)", () => {
  it("sends DELETE to /review/queue/resolved with vault_id", async () => {
    const resp: ReviewClearResolvedResponse = { deleted: 7 };
    const fetchMock = mockFetch(resp);
    vi.stubGlobal("fetch", fetchMock);

    const result = await clearResolved("default");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, { method: string }];
    expect(url).toContain("/review/queue/resolved");
    expect(url).toContain("vault_id=default");
    expect(init.method).toBe("DELETE");
    expect(result.deleted).toBe(7);
  });

  it("throws ApiError on non-ok response", async () => {
    const fetchMock = mockFetch({ detail: "Server error" }, 500);
    vi.stubGlobal("fetch", fetchMock);
    await expect(clearResolved("default")).rejects.toBeInstanceOf(ApiError);
  });
});
