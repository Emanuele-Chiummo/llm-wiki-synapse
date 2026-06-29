/**
 * researchStore.test.ts — unit tests for the deep-research Zustand store (F10, ADR-0024).
 *
 * Tests: fetchFresh, fetchMore, selectRun, startRun, startPollingDetail,
 *        terminal-status detection, polling cleanup.
 * All fetch calls are mocked — no real network.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { useResearchStore, isTerminal } from "../store/researchStore";
import type { ResearchRunListResponse, ResearchRunDetail, ResearchStartResponse } from "../api/types";

// ─── Helpers ──────────────────────────────────────────────────────────────────

function makeRun(
  overrides: Partial<{
    id: string;
    topic: string;
    status: string;
    total_cost_usd: number;
    iterations_used: number;
  }> = {},
) {
  return {
    id: overrides.id ?? "run-1",
    vault_id: "default",
    topic: overrides.topic ?? "Test topic",
    status: overrides.status ?? "converged",
    iterations_used: overrides.iterations_used ?? 2,
    sources_fetched: 3,
    total_cost_usd: overrides.total_cost_usd ?? 0.0012,
    started_at: "2026-06-29T10:00:00Z",
    completed_at: "2026-06-29T10:01:00Z",
  };
}

function makeDetail(overrides: Partial<ResearchRunDetail> = {}): ResearchRunDetail {
  return {
    id: "run-1",
    vault_id: "default",
    topic: "Test topic",
    status: "running",
    max_iter: 3,
    token_budget: 100000,
    iterations_used: 1,
    queries_used: ["query A"],
    sources_fetched: 2,
    total_cost_usd: 0.0005,
    synthesis_text: null,
    synthesis_page_id: null,
    sources: [],
    started_at: "2026-06-29T10:00:00Z",
    completed_at: null,
    error_message: null,
    ...overrides,
  };
}

function mockFetch(body: unknown, status = 200): void {
  vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? "OK" : "Error",
    json: () => Promise.resolve(body),
  } as Response);
}

function mockFetchList(items: ReturnType<typeof makeRun>[], total?: number): void {
  const listResponse: ResearchRunListResponse = {
    items: items as ResearchRunListResponse["items"],
    total: total ?? items.length,
    limit: 20,
    offset: 0,
  };
  mockFetch(listResponse);
}

// Reset the store before each test
beforeEach(() => {
  useResearchStore.setState({
    runs: [],
    total: 0,
    offset: 0,
    listLoading: false,
    listError: null,
    selectedRunId: null,
    detail: null,
    detailLoading: false,
    detailError: null,
    runningCount: 0,
    starting: false,
    startError: null,
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ─── isTerminal ───────────────────────────────────────────────────────────────

describe("isTerminal", () => {
  it("returns true for terminal statuses", () => {
    expect(isTerminal("converged")).toBe(true);
    expect(isTerminal("max_iter_reached")).toBe(true);
    expect(isTerminal("budget_exhausted")).toBe(true);
    expect(isTerminal("error")).toBe(true);
  });

  it("returns false for running status", () => {
    expect(isTerminal("running")).toBe(false);
  });
});

// ─── fetchFresh ───────────────────────────────────────────────────────────────

describe("fetchFresh", () => {
  it("populates runs and total", async () => {
    const runs = [makeRun({ id: "r1", status: "converged" })];
    mockFetchList(runs, 1);

    await useResearchStore.getState().fetchFresh();

    const state = useResearchStore.getState();
    expect(state.runs).toHaveLength(1);
    expect(state.total).toBe(1);
    expect(state.listLoading).toBe(false);
    expect(state.listError).toBe(null);
  });

  it("sets listError on fetch failure", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(new Error("Network error"));

    await useResearchStore.getState().fetchFresh();

    const state = useResearchStore.getState();
    expect(state.listError).toBe("Network error");
    expect(state.listLoading).toBe(false);
  });

  it("ignores AbortError", async () => {
    const abortErr = new Error("aborted");
    abortErr.name = "AbortError";
    vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(abortErr);

    await useResearchStore.getState().fetchFresh();

    expect(useResearchStore.getState().listError).toBe(null);
  });

  it("counts running runs correctly", async () => {
    const runs = [
      makeRun({ id: "r1", status: "running" }),
      makeRun({ id: "r2", status: "converged" }),
      makeRun({ id: "r3", status: "running" }),
    ];
    mockFetchList(runs, 3);

    await useResearchStore.getState().fetchFresh();

    expect(useResearchStore.getState().runningCount).toBe(2);
  });
});

// ─── fetchMore ────────────────────────────────────────────────────────────────

describe("fetchMore", () => {
  it("appends runs to existing list", async () => {
    useResearchStore.setState({
      runs: [makeRun({ id: "r1" })] as ResearchRunListResponse["items"],
      total: 2,
      offset: 0,
    });
    mockFetchList([makeRun({ id: "r2" })], 2);

    await useResearchStore.getState().fetchMore();

    const state = useResearchStore.getState();
    expect(state.runs).toHaveLength(2);
    expect(state.runs.map((r) => r.id)).toEqual(["r1", "r2"]);
  });

  it("does not fetch if all runs already loaded", async () => {
    useResearchStore.setState({
      runs: [makeRun({ id: "r1" })] as ResearchRunListResponse["items"],
      total: 1,
      offset: 0,
    });
    const spy = vi.spyOn(globalThis, "fetch");

    await useResearchStore.getState().fetchMore();

    expect(spy).not.toHaveBeenCalled();
  });
});

// ─── selectRun ────────────────────────────────────────────────────────────────

describe("selectRun", () => {
  it("loads detail for the selected run", async () => {
    const detail = makeDetail({ id: "run-1", status: "converged", synthesis_text: "Summary" });
    mockFetch(detail);

    await useResearchStore.getState().selectRun("run-1");

    const state = useResearchStore.getState();
    expect(state.selectedRunId).toBe("run-1");
    expect(state.detail).not.toBeNull();
    expect(state.detail?.synthesis_text).toBe("Summary");
    expect(state.detailLoading).toBe(false);
  });

  it("clears detail when called with null", async () => {
    useResearchStore.setState({ selectedRunId: "run-1", detail: makeDetail() });

    await useResearchStore.getState().selectRun(null);

    const state = useResearchStore.getState();
    expect(state.selectedRunId).toBe(null);
    expect(state.detail).toBe(null);
  });

  it("sets detailError on fetch failure", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(new Error("404 Not found"));

    await useResearchStore.getState().selectRun("bad-id");

    const state = useResearchStore.getState();
    expect(state.detailError).toBe("404 Not found");
    expect(state.detailLoading).toBe(false);
  });
});

// ─── startRun ─────────────────────────────────────────────────────────────────

describe("startRun", () => {
  it("POSTs and then fetches the new run, returning run_id", async () => {
    // Chain all three fetch responses on the same spy (one spy, three queued responses)
    const startResponse: ResearchStartResponse = { run_id: "new-run-1" };
    const listResponse: ResearchRunListResponse = {
      items: [makeRun({ id: "new-run-1", status: "running" })] as ResearchRunListResponse["items"],
      total: 1,
      limit: 20,
      offset: 0,
    };
    const detailResponse = makeDetail({ id: "new-run-1", status: "running" });

    const spy = vi.spyOn(globalThis, "fetch");
    spy
      .mockResolvedValueOnce({ ok: true, status: 202, json: () => Promise.resolve(startResponse) } as Response)
      .mockResolvedValueOnce({ ok: true, status: 200, json: () => Promise.resolve(listResponse) } as Response)
      .mockResolvedValueOnce({ ok: true, status: 200, json: () => Promise.resolve(detailResponse) } as Response);

    const runId = await useResearchStore.getState().startRun({
      vault_id: "default",
      topic: "New topic",
    });

    expect(runId).toBe("new-run-1");
    const state = useResearchStore.getState();
    expect(state.starting).toBe(false);
    expect(state.startError).toBe(null);
    expect(state.selectedRunId).toBe("new-run-1");
  });

  it("sets startError on failure", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(new Error("503 SEARXNG not configured"));

    await expect(
      useResearchStore.getState().startRun({ vault_id: "default", topic: "test" })
    ).rejects.toThrow();

    const state = useResearchStore.getState();
    expect(state.startError).toBe("503 SEARXNG not configured");
    expect(state.starting).toBe(false);
  });
});

// ─── startPollingDetail ───────────────────────────────────────────────────────

describe("startPollingDetail", () => {
  it("returns a cleanup function", () => {
    const stop = useResearchStore.getState().startPollingDetail("run-1");
    expect(typeof stop).toBe("function");
    stop(); // cleanup — should not throw
  });

  it("stops polling after cleanup is called (aborted = no further fetch)", async () => {
    vi.useFakeTimers();
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(makeDetail({ id: "run-1", status: "running" })),
    } as Response);

    // Select the run first so the store knows which run is active
    useResearchStore.setState({
      selectedRunId: "run-1",
      detail: makeDetail({ id: "run-1", status: "running" }),
    });

    const stop = useResearchStore.getState().startPollingDetail("run-1");
    stop(); // abort immediately

    // Advance timer — fetch should NOT have been called (aborted before tick)
    await vi.advanceTimersByTimeAsync(6000);
    expect(spy).not.toHaveBeenCalled();

    vi.useRealTimers();
  });

  it("stops polling when status transitions to terminal", async () => {
    vi.useFakeTimers();
    let callCount = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => {
      callCount++;
      const status = callCount === 1 ? "converged" : "running";
      return {
        ok: true,
        status: 200,
        json: () => Promise.resolve(makeDetail({ id: "run-1", status })),
      } as Response;
    });

    useResearchStore.setState({
      selectedRunId: "run-1",
      detail: makeDetail({ id: "run-1", status: "running" }),
    });

    const stop = useResearchStore.getState().startPollingDetail("run-1");

    // First tick — fires fetch which returns "converged" (terminal)
    await vi.advanceTimersByTimeAsync(5001);
    // Flush all pending microtasks (Promise chains inside tick())
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    // Second tick interval passes — should NOT fire because status is now terminal
    await vi.advanceTimersByTimeAsync(5001);
    await Promise.resolve();
    await Promise.resolve();

    expect(callCount).toBe(1); // only 1 fetch (the one that returned converged)
    stop();
    vi.useRealTimers();
  });
});
