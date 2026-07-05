/**
 * ingest-cancel-client.test.ts — unit tests for cancelIngestRun (R13-3).
 *
 * Contract: DELETE /ingest/{run_id}
 *   202 → {"status":"cancelling", "run_id":"...", "cleaned_pages": N}
 *   200 → {"status":"cancelled", "run_id":"..."}
 *   404 → ApiError (unknown run)
 *   409 → ApiError (already terminal)
 *
 * Mocks globalThis.fetch — no real network calls.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { cancelIngestRun } from "../api/ingestClient";
import { ApiError } from "../api/graphClient";

// ─── Helpers ──────────────────────────────────────────────────────────────────

function mockFetch(status: number, body: unknown): void {
  vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? "OK" : status === 202 ? "Accepted" : "Error",
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response);
}

interface FetchInit {
  method?: string;
  signal?: AbortSignal;
}

// ─── 202 — running run signalled ("cancelling") ───────────────────────────────

describe("cancelIngestRun — 202 cancelling (running run)", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("sends DELETE to /ingest/{id}", async () => {
    mockFetch(202, { run_id: "run-abc", status: "cancelling", cleaned_pages: 2 });

    await cancelIngestRun("run-abc");

    const [url, init] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      FetchInit,
    ];
    expect(url).toContain("/ingest/run-abc");
    expect(url).not.toContain("/cancel");
    expect(init.method).toBe("DELETE");
  });

  it("resolves with status:'cancelling' on 202", async () => {
    mockFetch(202, { run_id: "run-abc", status: "cancelling", cleaned_pages: 2 });

    const result = await cancelIngestRun("run-abc");

    expect(result.status).toBe("cancelling");
    expect(result.run_id).toBe("run-abc");
    expect(result.cleaned_pages).toBe(2);
  });

  it("URL-encodes the run_id", async () => {
    mockFetch(202, { run_id: "run abc", status: "cancelling" });

    await cancelIngestRun("run abc");

    const [url] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string];
    expect(url).toContain("run%20abc");
  });
});

// ─── 200 — queued run cancelled immediately ───────────────────────────────────

describe("cancelIngestRun — 200 cancelled (queued run)", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("resolves with status:'cancelled' on 200", async () => {
    mockFetch(200, { run_id: "run-xyz", status: "cancelled" });

    const result = await cancelIngestRun("run-xyz");

    expect(result.status).toBe("cancelled");
    expect(result.run_id).toBe("run-xyz");
  });
});

// ─── 404 — unknown run ────────────────────────────────────────────────────────

describe("cancelIngestRun — 404 unknown run", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("throws ApiError with status 404", async () => {
    mockFetch(404, { detail: "Run not found" });

    await expect(cancelIngestRun("missing-run")).rejects.toBeInstanceOf(ApiError);
  });

  it("ApiError.status is 404", async () => {
    mockFetch(404, { detail: "Run not found" });

    try {
      await cancelIngestRun("missing-run");
      expect.fail("expected ApiError");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).status).toBe(404);
    }
  });
});

// ─── 409 — already terminal ───────────────────────────────────────────────────

describe("cancelIngestRun — 409 already terminal", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("throws ApiError with status 409 for already-terminal runs", async () => {
    mockFetch(409, { detail: "Run already terminal" });

    await expect(cancelIngestRun("done-run")).rejects.toBeInstanceOf(ApiError);
  });

  it("ApiError.status is 409", async () => {
    mockFetch(409, { detail: "Run already terminal" });

    try {
      await cancelIngestRun("done-run");
      expect.fail("expected ApiError");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).status).toBe(409);
    }
  });
});

// ─── AbortSignal forwarding ───────────────────────────────────────────────────

describe("cancelIngestRun — AbortSignal forwarding", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("passes signal to fetch when provided", async () => {
    mockFetch(202, { run_id: "r1", status: "cancelling", cleaned_pages: 0 });

    const ctrl = new AbortController();
    await cancelIngestRun("r1", ctrl.signal);

    const [, init] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      FetchInit,
    ];
    expect(init.signal).toBe(ctrl.signal);
  });

  it("omits signal key when no signal provided", async () => {
    mockFetch(202, { run_id: "r1", status: "cancelling", cleaned_pages: 0 });

    await cancelIngestRun("r1");

    const [, init] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      FetchInit,
    ];
    expect(init.signal).toBeUndefined();
  });
});
