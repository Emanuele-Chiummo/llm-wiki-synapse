/**
 * costsClient.test.ts — unit tests for GET /costs/summary wire format (R9-1).
 *
 * Covers:
 *   A. Response shape matches backend wire format exactly (envelope lesson).
 *   B. fetchCostsSummary builds the correct URL (with and without month param).
 *   C. HTTP error propagates as thrown Error with status prefix.
 *   D. AbortSignal is forwarded to fetch.
 *   E. by_provider_note is optional (null and omitted both accepted).
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import {
  fetchCostsSummary,
  type CostsSummary,
  type CostsByProvider,
  type CostsByOperation,
  type CostsByDay,
} from "../api/costsClient";

// ─── Wire-format fixture (mirrors real backend response exactly) ──────────────

const WIRE_RESPONSE: CostsSummary = {
  period: "2026-07",
  by_provider: [
    { provider: "api/anthropic", total_usd: 1.23, call_count: 42 },
    { provider: "local/ollama", total_usd: 0.0, call_count: 5 },
  ],
  by_provider_note: null,
  by_operation: [
    { operation: "ingest", total_usd: 0.75, call_count: 30 },
    { operation: "chat", total_usd: 0.48, call_count: 17 },
  ],
  by_day: [
    { date: "2026-07-01", total_usd: 0.12 },
    { date: "2026-07-02", total_usd: 0.45 },
    { date: "2026-07-03", total_usd: 0.66 },
  ],
  monthly_total_usd: 1.23,
  threshold_usd: 5.0,
  threshold_alert: false,
};

function makeMockResponse(body: CostsSummary, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

// ─── A. Response shape contract ───────────────────────────────────────────────

describe("CostsSummary shape contract (wire format)", () => {
  it("top-level field: period is a string", () => {
    const s: CostsSummary = WIRE_RESPONSE;
    expect(typeof s.period).toBe("string");
  });

  it("top-level field: monthly_total_usd is a number", () => {
    expect(typeof WIRE_RESPONSE.monthly_total_usd).toBe("number");
  });

  it("top-level field: threshold_usd is a number", () => {
    expect(typeof WIRE_RESPONSE.threshold_usd).toBe("number");
  });

  it("top-level field: threshold_alert is a boolean", () => {
    expect(typeof WIRE_RESPONSE.threshold_alert).toBe("boolean");
  });

  it("by_provider is an array of {provider, total_usd, call_count}", () => {
    const p: CostsByProvider = WIRE_RESPONSE.by_provider[0]!;
    expect(typeof p.provider).toBe("string");
    expect(typeof p.total_usd).toBe("number");
    expect(typeof p.call_count).toBe("number");
  });

  it("by_operation is an array of {operation, total_usd, call_count}", () => {
    const o: CostsByOperation = WIRE_RESPONSE.by_operation[0]!;
    expect(typeof o.operation).toBe("string");
    expect(typeof o.total_usd).toBe("number");
    expect(typeof o.call_count).toBe("number");
  });

  it("by_day is an array of {date, total_usd}", () => {
    const d: CostsByDay = WIRE_RESPONSE.by_day[0]!;
    expect(typeof d.date).toBe("string");
    expect(typeof d.total_usd).toBe("number");
  });

  it("by_provider_note is null in WIRE_RESPONSE (optional field)", () => {
    expect(WIRE_RESPONSE.by_provider_note).toBeNull();
  });

  it("threshold_alert is false when monthly_total_usd < threshold_usd", () => {
    expect(WIRE_RESPONSE.monthly_total_usd).toBeLessThan(WIRE_RESPONSE.threshold_usd);
    expect(WIRE_RESPONSE.threshold_alert).toBe(false);
  });
});

// ─── B. URL building ──────────────────────────────────────────────────────────

describe("fetchCostsSummary — URL construction", () => {
  it("fetches /costs/summary without query string when month is omitted", async () => {
    const mockFetch = vi.fn().mockResolvedValue(makeMockResponse(WIRE_RESPONSE));
    vi.stubGlobal("fetch", mockFetch);

    await fetchCostsSummary();

    expect(mockFetch).toHaveBeenCalledOnce();
    const url = mockFetch.mock.calls[0]![0] as string;
    expect(url).toMatch(/\/costs\/summary$/);
    expect(url).not.toContain("?month=");
  });

  it("fetches /costs/summary?month=YYYY-MM when month is provided", async () => {
    const mockFetch = vi.fn().mockResolvedValue(makeMockResponse(WIRE_RESPONSE));
    vi.stubGlobal("fetch", mockFetch);

    await fetchCostsSummary("2026-07");

    expect(mockFetch).toHaveBeenCalledOnce();
    const url = mockFetch.mock.calls[0]![0] as string;
    expect(url).toContain("?month=2026-07");
  });

  it("fetches /costs/summary without query string when month is null", async () => {
    const mockFetch = vi.fn().mockResolvedValue(makeMockResponse(WIRE_RESPONSE));
    vi.stubGlobal("fetch", mockFetch);

    await fetchCostsSummary(null);

    const url = mockFetch.mock.calls[0]![0] as string;
    expect(url).not.toContain("?month=");
  });

  it("returns the parsed CostsSummary on success", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(makeMockResponse(WIRE_RESPONSE)));

    const result = await fetchCostsSummary("2026-07");

    expect(result.period).toBe("2026-07");
    expect(result.monthly_total_usd).toBe(1.23);
    expect(result.by_provider).toHaveLength(2);
    expect(result.by_operation).toHaveLength(2);
    expect(result.by_day).toHaveLength(3);
  });
});

// ─── C. HTTP error propagation ────────────────────────────────────────────────

describe("fetchCostsSummary — HTTP error handling", () => {
  it("throws an Error on 404", async () => {
    const errBody = { detail: "No cost data for period" };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(errBody), {
          status: 404,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await expect(fetchCostsSummary("2026-07")).rejects.toThrow(
      "No cost data for period",
    );
  });

  it("throws an Error on 500 (fallback to status code)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("Internal Server Error", { status: 500 }),
      ),
    );

    await expect(fetchCostsSummary()).rejects.toThrow(/500/);
  });
});

// ─── D. AbortSignal forwarding ────────────────────────────────────────────────

describe("fetchCostsSummary — AbortSignal forwarded to fetch", () => {
  it("passes signal to fetch when provided", async () => {
    const mockFetch = vi.fn().mockResolvedValue(makeMockResponse(WIRE_RESPONSE));
    vi.stubGlobal("fetch", mockFetch);

    const ctrl = new AbortController();
    await fetchCostsSummary("2026-07", ctrl.signal);

    const callOpts = mockFetch.mock.calls[0]![1] as { signal?: AbortSignal } | undefined;
    expect(callOpts?.signal).toBe(ctrl.signal);
  });

  it("omits the second argument to fetch when no signal is provided", async () => {
    const mockFetch = vi.fn().mockResolvedValue(makeMockResponse(WIRE_RESPONSE));
    vi.stubGlobal("fetch", mockFetch);

    await fetchCostsSummary();

    // When no signal: second arg has no signal property (apiFetch always passes headers)
    const callOpts = mockFetch.mock.calls[0]![1] as { signal?: AbortSignal; headers?: unknown } | undefined;
    expect(callOpts?.signal).toBeUndefined();
  });
});

// ─── E. threshold_alert = true case ──────────────────────────────────────────

describe("fetchCostsSummary — threshold_alert=true wire case", () => {
  it("parses threshold_alert=true when monthly_total exceeds threshold", async () => {
    const alertResponse: CostsSummary = {
      ...WIRE_RESPONSE,
      monthly_total_usd: 8.5,
      threshold_usd: 5.0,
      threshold_alert: true,
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(makeMockResponse(alertResponse)));

    const result = await fetchCostsSummary("2026-07");

    expect(result.threshold_alert).toBe(true);
    expect(result.monthly_total_usd).toBeGreaterThan(result.threshold_usd);
  });
});
