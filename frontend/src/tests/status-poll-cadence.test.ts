/**
 * status-poll-cadence.test.ts — 1.8.1 (RT-1): the /status poll cadence must be fast while the
 * queue is doing work (so data_version, and thus the dashboard KPIs + graph, stay live) and fall
 * back to the idle interval otherwise.
 */

import { describe, it, expect } from "vitest";
import type { IngestQueueSnapshot } from "../api/types";
import { statusPollDelayMs } from "../components/activity/ActivityBar";

function snap(overrides: Partial<IngestQueueSnapshot>): IngestQueueSnapshot {
  return { processing: 0, pending: 0, paused: false, tasks: [], ...overrides } as IngestQueueSnapshot;
}

describe("statusPollDelayMs (RT-1)", () => {
  it("polls fast while processing", () => {
    expect(statusPollDelayMs(snap({ processing: 1 }))).toBe(3_000);
  });
  it("polls fast while pending", () => {
    expect(statusPollDelayMs(snap({ pending: 2 }))).toBe(3_000);
  });
  it("polls slow when idle", () => {
    expect(statusPollDelayMs(snap({}))).toBe(30_000);
    expect(statusPollDelayMs(null)).toBe(30_000);
  });
  it("a paused-but-empty queue is idle (no writes → no need to poll fast)", () => {
    expect(statusPollDelayMs(snap({ paused: true }))).toBe(30_000);
  });
});
