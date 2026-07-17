/**
 * researchClient.test.ts — unit tests for the deep-research API client (F10, ADR-0024 §8).
 *
 * Mocks globalThis.fetch — no real network calls.
 * Tests: optimizeResearchTopic, startResearch, fetchResearchRuns, fetchResearchRunDetail.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  optimizeResearchTopic,
  startResearch,
  fetchResearchRuns,
  fetchResearchRunDetail,
} from "../api/researchClient";
import type {
  ResearchRunListResponse,
  ResearchRunDetail,
  ResearchStartResponse,
} from "../api/types";

// Inline type for fetch init to avoid ESLint no-undef on the DOM global FetchInit
interface FetchInit {
  method?: string;
  headers?: Record<string, string>;
  body?: unknown;
  signal?: AbortSignal;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function mockFetch(body: unknown, status = 200): void {
  vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? "OK" : "Error",
    json: () => Promise.resolve(body),
  } as Response);
}

// ─── optimizeResearchTopic (B5/D3) ────────────────────────────────────────────

describe("optimizeResearchTopic", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("POSTs to /research/optimize-topic and returns optimized_topic + queries", async () => {
    mockFetch({
      optimized_topic: "Kubernetes CNI deep dive",
      queries: ["Kubernetes CNI comparison", "Calico vs Cilium"],
    });

    const result = await optimizeResearchTopic("Kubernetes networking");

    expect(globalThis.fetch).toHaveBeenCalledOnce();
    const [url, opts] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      FetchInit,
    ];
    expect(url).toContain("/research/optimize-topic");
    expect(opts.method).toBe("POST");
    expect(JSON.parse(opts.body as string)).toMatchObject({ topic: "Kubernetes networking" });
    expect(result.optimized_topic).toBe("Kubernetes CNI deep dive");
    expect(result.queries).toHaveLength(2);
  });

  it("passes AbortSignal to fetch", async () => {
    mockFetch({ optimized_topic: "test", queries: [] });
    const ctrl = new AbortController();

    await optimizeResearchTopic("test topic", ctrl.signal);

    const [, opts] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      FetchInit,
    ];
    expect(opts?.signal).toBe(ctrl.signal);
  });

  it("throws ApiError on non-2xx response", async () => {
    mockFetch({ detail: "Provider not configured" }, 503);
    await expect(optimizeResearchTopic("test")).rejects.toThrow("503");
  });

  it("returns echo response when provider unavailable (graceful degradation)", async () => {
    // Backend echoes the seed topic with naive queries on 200
    mockFetch({ optimized_topic: "Kubernetes networking", queries: ["Kubernetes networking"] });

    const result = await optimizeResearchTopic("Kubernetes networking");
    expect(result.optimized_topic).toBe("Kubernetes networking");
    expect(result.queries).toHaveLength(1);
  });
});

// ─── startResearch ────────────────────────────────────────────────────────────

describe("startResearch", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("POSTs to /research/start and returns run_id on 202", async () => {
    const mockResponse: ResearchStartResponse = { run_id: "abc-123" };
    mockFetch(mockResponse, 202);

    const result = await startResearch({ vault_id: "default", topic: "Kubernetes networking" });

    expect(globalThis.fetch).toHaveBeenCalledOnce();
    const [url, opts] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      FetchInit,
    ];
    expect(url).toContain("/research/start");
    expect(opts.method).toBe("POST");
    expect(JSON.parse(opts.body as string)).toMatchObject({ topic: "Kubernetes networking" });
    expect(result.run_id).toBe("abc-123");
  });

  it("includes optional max_iter and token_budget in body when provided", async () => {
    mockFetch({ run_id: "xyz-456" }, 202);

    await startResearch({
      vault_id: "default",
      topic: "test",
      max_iter: 2,
      token_budget: 50000,
    });

    const [, opts] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      FetchInit,
    ];
    const body = JSON.parse(opts.body as string) as Record<string, unknown>;
    expect(body["max_iter"]).toBe(2);
    expect(body["token_budget"]).toBe(50000);
  });

  it("includes optional queries field in body when provided (B5/D3)", async () => {
    mockFetch({ run_id: "xyz-789" }, 202);

    await startResearch({
      vault_id: "default",
      topic: "Kubernetes networking",
      queries: ["Kubernetes CNI comparison", "Calico vs Cilium"],
    });

    const [, opts] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      FetchInit,
    ];
    const body = JSON.parse(opts.body as string) as Record<string, unknown>;
    expect(body["queries"]).toEqual(["Kubernetes CNI comparison", "Calico vs Cilium"]);
  });

  it("throws ApiError on non-2xx response", async () => {
    mockFetch({ detail: "SEARXNG_URL not configured" }, 503);
    await expect(startResearch({ vault_id: "v", topic: "t" })).rejects.toThrow("503");
  });
});

// ─── fetchResearchRuns ────────────────────────────────────────────────────────

describe("fetchResearchRuns", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("GETs /research/runs with limit and offset defaults", async () => {
    const mockList: ResearchRunListResponse = {
      items: [],
      total: 0,
      limit: 20,
      offset: 0,
    };
    mockFetch(mockList);

    const result = await fetchResearchRuns();

    const [url] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string];
    expect(url).toContain("/research/runs");
    expect(url).toContain("limit=20");
    expect(url).toContain("offset=0");
    expect(result.items).toEqual([]);
  });

  it("appends vault_id when provided", async () => {
    mockFetch({ items: [], total: 0, limit: 20, offset: 0 });

    await fetchResearchRuns({ vaultId: "my-vault" });

    const [url] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string];
    expect(url).toContain("vault_id=my-vault");
  });

  it("returns items from the response", async () => {
    const run = {
      id: "run-1",
      vault_id: "default",
      topic: "test topic",
      status: "converged",
      iterations_used: 2,
      sources_fetched: 5,
      total_cost_usd: 0.0012,
      started_at: "2026-06-29T10:00:00Z",
      completed_at: "2026-06-29T10:01:00Z",
    };
    mockFetch({ items: [run], total: 1, limit: 20, offset: 0 });

    const result = await fetchResearchRuns();
    expect(result.items).toHaveLength(1);
    expect(result.items[0]?.topic).toBe("test topic");
    expect(result.total).toBe(1);
  });

  it("forwards AbortSignal to fetch", async () => {
    mockFetch({ items: [], total: 0, limit: 20, offset: 0 });
    const ctrl = new AbortController();

    await fetchResearchRuns({}, ctrl.signal);

    const [, opts] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      FetchInit,
    ];
    expect(opts?.signal).toBe(ctrl.signal);
  });
});

// ─── fetchResearchRunDetail ───────────────────────────────────────────────────

describe("fetchResearchRunDetail", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("GETs /research/runs/{id}", async () => {
    const mockDetail: ResearchRunDetail = {
      id: "run-1",
      vault_id: "default",
      topic: "Test",
      status: "converged",
      max_iter: 3,
      token_budget: 100000,
      iterations_used: 2,
      queries_used: ["query A", "query B"],
      sources_fetched: 4,
      total_cost_usd: 0.0042,
      synthesis_text: "# Summary\nSome content.",
      synthesis_page_id: null,
      sources: [
        { url: "https://example.com", title: "Example", relevance_score: 0.9, iteration: 1 },
      ],
      started_at: "2026-06-29T10:00:00Z",
      completed_at: "2026-06-29T10:01:00Z",
      error_message: null,
    };
    mockFetch(mockDetail);

    const result = await fetchResearchRunDetail("run-1");

    const [url] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string];
    expect(url).toContain("/research/runs/run-1");
    expect(result.topic).toBe("Test");
    expect(result.synthesis_text).toBe("# Summary\nSome content.");
    expect(result.sources).toHaveLength(1);
    expect(result.queries_used).toHaveLength(2);
  });

  it("throws ApiError on 404", async () => {
    mockFetch({ detail: "Not found" }, 404);
    await expect(fetchResearchRunDetail("bad-id")).rejects.toThrow("404");
  });
});
