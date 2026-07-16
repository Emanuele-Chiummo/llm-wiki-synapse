/**
 * ingest-polling-store.test.ts — 1.8.1 regression: startPolling must not freeze/halt on
 * running rows that sit past the first page.
 *
 * Bug: the poll fetched only the first page (PAGE_LIMIT=20, offset 0) and computed runningCount
 * from that page alone. So a running row beyond position 20 never refreshed, and if the first page
 * had no running rows the poll set runningCount=0 and the `prevRunning===0` guard halted polling —
 * freezing every deeper running row at "running" / PAGINE CREATE 0.
 *
 * Fix: re-fetch the full loaded range and compute runningCount over the WHOLE merged list.
 *
 * ingestClient is mocked — no real HTTP.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { IngestRunItem } from "../api/types";

const mockFetchIngestRuns = vi.fn();

vi.mock("../api/ingestClient", () => ({
  cancelIngestRun: vi.fn(),
  fetchIngestRuns: (...args: unknown[]) => mockFetchIngestRuns(...args),
}));

vi.mock("../api/base", () => ({ apiBase: () => "", isTauri: () => false }));

import { useIngestStore } from "../store/ingestStore";

const POLL_INTERVAL_MS = 5_000;

function makeRun(id: string, status: IngestRunItem["status"]): IngestRunItem {
  return {
    id,
    vault_id: "default",
    status,
    provider_type: "cli",
    pages_created: 0,
    iterations_used: 0,
    total_cost_usd: 0,
    started_at: "2026-07-16T10:00:00Z",
    completed_at: null,
    error_message: null,
  };
}

describe("ingestStore.startPolling — deep running rows", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mockFetchIngestRuns.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("keeps runningCount>0 and reschedules when the running row is past the first page", async () => {
    // 21 loaded runs: first 20 terminal, the 21st still running.
    const runs = Array.from({ length: 20 }, (_, i) => makeRun(`r${i}`, "completed"));
    runs.push(makeRun("r20", "running"));
    useIngestStore.setState({
      runs,
      total: 21,
      offset: 0,
      loading: false,
      error: null,
      selectedRunId: null,
      runningCount: 1,
    });

    // Simulate a backend that returns only the first-page worth of terminal items.
    mockFetchIngestRuns.mockResolvedValue({ items: runs.slice(0, 20), total: 21 });

    const stop = useIngestStore.getState().startPolling(undefined);
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS); // first tick

    // Computed over the FULL merged list (incl. the deeper running row) → stays 1, must not halt.
    expect(useIngestStore.getState().runningCount).toBe(1);

    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS); // must reschedule → second fetch
    expect(mockFetchIngestRuns).toHaveBeenCalledTimes(2);
    stop();
  });

  it("re-fetches the full loaded range, not just the first page", async () => {
    const runs = Array.from({ length: 25 }, (_, i) =>
      makeRun(`r${i}`, i === 24 ? "running" : "completed"),
    );
    useIngestStore.setState({
      runs,
      total: 25,
      offset: 0,
      loading: false,
      error: null,
      selectedRunId: null,
      runningCount: 1,
    });
    mockFetchIngestRuns.mockResolvedValue({ items: runs, total: 25 });

    const stop = useIngestStore.getState().startPolling(undefined);
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);

    // limit must cover all loaded rows (>=25), not the 20-row first page.
    expect(mockFetchIngestRuns).toHaveBeenCalledWith(
      expect.objectContaining({ limit: 25, offset: 0 }),
      expect.anything(),
    );
    stop();
  });
});
