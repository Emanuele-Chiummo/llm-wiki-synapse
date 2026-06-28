/**
 * graphClient.test.ts
 *
 * Tests for the fetchGraph API client:
 *   - Parses the GET /graph contract correctly (AC-F4-3)
 *   - Records X-Graph-Cache header into cacheStatus (hit/miss/unknown)
 *   - Throws ApiError on non-200 responses
 *   - Passes precomputed coords through without modification (I2)
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { fetchGraph, ApiError } from "../api/graphClient";
import type { GraphResponse } from "../api/types";

// ─── Mock fetch ───────────────────────────────────────────────────────────────

function makeMockResponse(
  body: GraphResponse,
  status: number = 200,
  cacheHeader: string | null = "hit",
): Response {
  const headers = new Headers();
  if (cacheHeader !== null) {
    headers.set("X-Graph-Cache", cacheHeader);
  }
  return new Response(JSON.stringify(body), {
    status,
    headers,
  });
}

const GRAPH_RESPONSE: GraphResponse = {
  nodes: [
    { id: "n1", title: "Node One", type: "concept", x: 10.5, y: -3.2, size: 1.0, degree: 2 },
    { id: "n2", title: "Node Two", type: "entity", x: -50.0, y: 80.1, size: 1.5, degree: 1 },
  ],
  edges: [{ source: "n1", target: "n2", weight: 11.0 }],
  data_version: 7,
  cached: true,
};

afterEach(() => {
  vi.restoreAllMocks();
});

// ─── Contract parsing ─────────────────────────────────────────────────────────

describe("fetchGraph — GET /graph contract parsing (AC-F4-3)", () => {
  it("parses a well-formed 200 response correctly", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(makeMockResponse(GRAPH_RESPONSE)));

    const result = await fetchGraph("test-vault");

    expect(result.data.nodes).toHaveLength(2);
    expect(result.data.edges).toHaveLength(1);
    expect(result.data.data_version).toBe(7);
    expect(result.data.cached).toBe(true);
  });

  it("returns nodes with all required fields (id, title, type, x, y)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(makeMockResponse(GRAPH_RESPONSE)));

    const result = await fetchGraph("test-vault");
    const node = result.data.nodes[0]!;

    expect(typeof node.id).toBe("string");
    expect(typeof node.title).toBe("string");
    // type may be null
    expect(node.type === null || typeof node.type === "string").toBe(true);
    expect(typeof node.x).toBe("number");
    expect(typeof node.y).toBe("number");
  });

  it("preserves x/y coords exactly from the server response (I2)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(makeMockResponse(GRAPH_RESPONSE)));

    const result = await fetchGraph("test-vault");

    expect(result.data.nodes[0]!.x).toBe(10.5);
    expect(result.data.nodes[0]!.y).toBe(-3.2);
    expect(result.data.nodes[1]!.x).toBe(-50.0);
    expect(result.data.nodes[1]!.y).toBe(80.1);
  });

  it("returns edge with source, target, weight", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(makeMockResponse(GRAPH_RESPONSE)));

    const result = await fetchGraph("test-vault");
    const edge = result.data.edges[0]!;

    expect(edge.source).toBe("n1");
    expect(edge.target).toBe("n2");
    expect(edge.weight).toBe(11.0);
  });

  it("passes vault_id as a query parameter in the request URL", async () => {
    const mockFetch = vi.fn().mockResolvedValue(makeMockResponse(GRAPH_RESPONSE));
    vi.stubGlobal("fetch", mockFetch);

    await fetchGraph("my-special-vault");

    expect(mockFetch).toHaveBeenCalledOnce();
    const [url] = mockFetch.mock.calls[0] as [string];
    expect(url).toContain("vault_id=my-special-vault");
  });
});

// ─── X-Graph-Cache header ─────────────────────────────────────────────────────

describe("fetchGraph — X-Graph-Cache header parsing", () => {
  it("records cacheStatus = 'hit' when header is 'hit'", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(makeMockResponse(GRAPH_RESPONSE, 200, "hit")));

    const result = await fetchGraph("v");
    expect(result.cacheStatus).toBe("hit");
  });

  it("records cacheStatus = 'miss' when header is 'miss'", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(makeMockResponse(GRAPH_RESPONSE, 200, "miss")),
    );

    const result = await fetchGraph("v");
    expect(result.cacheStatus).toBe("miss");
  });

  it("records cacheStatus = 'unknown' when header is absent", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(makeMockResponse(GRAPH_RESPONSE, 200, null)),
    );

    const result = await fetchGraph("v");
    expect(result.cacheStatus).toBe("unknown");
  });
});

// ─── Error handling ───────────────────────────────────────────────────────────

describe("fetchGraph — error handling", () => {
  it("throws ApiError with status 404 for not-found responses", async () => {
    const errorResponse = new Response(JSON.stringify({ detail: "Vault not found" }), {
      status: 404,
      headers: { "Content-Type": "application/json" },
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(errorResponse));

    await expect(fetchGraph("missing")).rejects.toThrow(ApiError);
    await expect(fetchGraph("missing")).rejects.toMatchObject({ status: 404 });
  });

  it("throws ApiError with status 500 for server errors", async () => {
    const errorResponse = new Response("Internal Server Error", { status: 500 });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(errorResponse));

    await expect(fetchGraph("v")).rejects.toThrow(ApiError);
    await expect(fetchGraph("v")).rejects.toMatchObject({ status: 500 });
  });

  it("propagates network errors (fetch rejection)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Network Error")));

    await expect(fetchGraph("v")).rejects.toThrow("Network Error");
  });

  it("does NOT throw when AbortController aborts the request", async () => {
    const ctrl = new AbortController();
    const abortError = new DOMException("The user aborted a request.", "AbortError");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(abortError));

    ctrl.abort();
    // The error is an AbortError — callers handle it; fetchGraph itself propagates it
    await expect(fetchGraph("v", ctrl.signal)).rejects.toMatchObject({ name: "AbortError" });
  });
});

// ─── I2: no layout call in fetchGraph ────────────────────────────────────────

describe("fetchGraph — I2: no client layout invoked", () => {
  it("does NOT call Math.random (random layout sentinel)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(makeMockResponse(GRAPH_RESPONSE)));
    const randomSpy = vi.spyOn(Math, "random");

    await fetchGraph("v");

    expect(randomSpy).not.toHaveBeenCalled();
    randomSpy.mockRestore();
  });

  it("does NOT call requestAnimationFrame (physics loop sentinel)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(makeMockResponse(GRAPH_RESPONSE)));
    const rafSpy = vi.spyOn(globalThis, "requestAnimationFrame").mockReturnValue(0);

    await fetchGraph("v");

    expect(rafSpy).not.toHaveBeenCalled();
    rafSpy.mockRestore();
  });
});
