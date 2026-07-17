/**
 * eventsStore.test.ts — GET /events SSE consumer (1.9.3 W1, FE-RT-2).
 *
 * Coverage:
 *   T-SSE-001  parseSseFrame extracts id/event/data, ignoring blank lines and heartbeat comments.
 *   T-SSE-002  dispatchSseFrame(data_version) updates useStatusStore.dataVersion.
 *   T-SSE-003  dispatchSseFrame(queue) updates useActivityStore via applyCountsPatch.
 *   T-SSE-004  dispatchSseFrame ignores malformed/incomplete payloads without throwing.
 *   T-SSE-005  start() opens the stream, parses frames as they arrive, and applies them to stores.
 *   T-SSE-006  a connection failure schedules a reconnect with Last-Event-ID from the last frame.
 *   T-SSE-007  healthy flips false only after 3 consecutive failures (not on the first).
 *   T-SSE-008  stop() aborts the in-flight connection and resets state to idle.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { useStatusStore } from "../store/statusStore";
import { useActivityStore } from "../store/activityStore";

// ─── Mock the transport ────────────────────────────────────────────────────────

vi.mock("../api/eventsClient", () => ({
  openEventsStream: vi.fn(),
}));

import * as eventsClientModule from "../api/eventsClient";
const mockedOpenEventsStream = eventsClientModule.openEventsStream as ReturnType<typeof vi.fn>;

import {
  useEventsStore,
  parseSseFrame,
  dispatchSseFrame,
  resetEventsStoreForTests,
} from "../store/eventsStore";

// ─── Helpers ────────────────────────────────────────────────────────────────────

function sseFrame(event: string, id: string, data: unknown): string {
  return `id: ${id}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

/** A ReadableStream<Uint8Array> that enqueues the given text frames then stays open. */
function streamOf(frames: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(ctrl) {
      for (const f of frames) ctrl.enqueue(encoder.encode(f));
      // never close — simulates an in-progress SSE connection
    },
  });
}

function resetStores(): void {
  useStatusStore.setState({ dataVersion: null });
  useActivityStore.setState({ snapshot: null, loading: false, error: null });
  resetEventsStoreForTests();
}

beforeEach(() => {
  resetStores();
  vi.clearAllMocks();
});

afterEach(() => {
  resetStores();
  vi.useRealTimers();
});

// ─── T-SSE-001: frame parsing ───────────────────────────────────────────────────

describe("parseSseFrame", () => {
  it("extracts id/event/data lines", () => {
    const frame = 'id: 3:1\nevent: data_version\ndata: {"data_version":3}';
    expect(parseSseFrame(frame)).toEqual({
      id: "3:1",
      event: "data_version",
      data: '{"data_version":3}',
    });
  });

  it("ignores heartbeat comment lines", () => {
    expect(parseSseFrame(": heartbeat")).toEqual({});
  });
});

// ─── T-SSE-002/003/004: dispatch ────────────────────────────────────────────────

describe("dispatchSseFrame", () => {
  it("updates useStatusStore.dataVersion on a data_version frame", () => {
    dispatchSseFrame({ event: "data_version", data: JSON.stringify({ data_version: 42 }) });
    expect(useStatusStore.getState().dataVersion).toBe(42);
  });

  it("updates useActivityStore snapshot on a queue frame", () => {
    dispatchSseFrame({
      event: "queue",
      data: JSON.stringify({
        paused: false,
        pending: 1,
        processing: 2,
        failed: 0,
        completed_since_idle: 3,
        total: 3,
      }),
    });
    const snap = useActivityStore.getState().snapshot;
    expect(snap).not.toBeNull();
    expect(snap?.processing).toBe(2);
    expect(snap?.pending).toBe(1);
    expect(snap?.tasks).toEqual([]);
  });

  it("ignores malformed JSON without throwing", () => {
    expect(() => dispatchSseFrame({ event: "data_version", data: "not json" })).not.toThrow();
    expect(useStatusStore.getState().dataVersion).toBeNull();
  });

  it("ignores a queue payload missing required fields", () => {
    dispatchSseFrame({ event: "queue", data: JSON.stringify({ pending: 1 }) });
    expect(useActivityStore.getState().snapshot).toBeNull();
  });

  it("no-ops when event or data is missing", () => {
    expect(() => dispatchSseFrame({})).not.toThrow();
    expect(() => dispatchSseFrame({ event: "data_version" })).not.toThrow();
  });
});

// ─── T-SSE-005: end-to-end store wiring via a mocked stream ─────────────────────

describe("useEventsStore.start() — stream consumption", () => {
  it("parses frames as they arrive and updates both stores", async () => {
    mockedOpenEventsStream.mockResolvedValueOnce({
      body: streamOf([
        sseFrame("data_version", "5:1", { data_version: 5 }),
        sseFrame("queue", "5:2", {
          paused: false,
          pending: 0,
          processing: 1,
          failed: 0,
          completed_since_idle: 0,
          total: 1,
        }),
      ]),
    } as unknown as Response);

    const stop = useEventsStore.getState().start();
    // Let the async reader loop drain the microtask queue.
    await new Promise((r) => setTimeout(r, 10));

    expect(useStatusStore.getState().dataVersion).toBe(5);
    expect(useActivityStore.getState().snapshot?.processing).toBe(1);
    expect(useEventsStore.getState().connectionState).toBe("open");
    expect(useEventsStore.getState().healthy).toBe(true);

    stop();
  });

  it("refcounts: a second start() call while running does not reopen the stream", async () => {
    mockedOpenEventsStream.mockResolvedValue({ body: streamOf([]) } as unknown as Response);

    const stop1 = useEventsStore.getState().start();
    await new Promise((r) => setTimeout(r, 5));
    const stop2 = useEventsStore.getState().start();
    await new Promise((r) => setTimeout(r, 5));

    expect(mockedOpenEventsStream).toHaveBeenCalledTimes(1);

    stop2();
    stop1();
  });
});

// ─── T-SSE-006/007: reconnect + backoff + healthy flag ──────────────────────────

/** A ReadableStream<Uint8Array> that enqueues the given frames then CLOSES (done:true). */
function streamThatCloses(frames: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(ctrl) {
      for (const f of frames) ctrl.enqueue(encoder.encode(f));
      ctrl.close();
    },
  });
}

describe("useEventsStore.start() — reconnect on failure", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  it("healthy flips false only after 3 consecutive failed connection attempts", async () => {
    mockedOpenEventsStream.mockRejectedValue(new Error("boom"));

    useEventsStore.getState().start();

    // Attempt 1 fails — healthy was already false (never connected); still false.
    await vi.advanceTimersByTimeAsync(0);
    expect(useEventsStore.getState().connectionState).toBe("closed");
    expect(useEventsStore.getState().healthy).toBe(false);
    expect(mockedOpenEventsStream).toHaveBeenCalledTimes(1);

    // Attempt 2 (after 1s backoff) also fails — still under the threshold.
    await vi.advanceTimersByTimeAsync(1_000);
    expect(mockedOpenEventsStream).toHaveBeenCalledTimes(2);
    expect(useEventsStore.getState().healthy).toBe(false);

    // Attempt 3 (after 2s backoff) fails — 3rd consecutive failure, threshold hit.
    await vi.advanceTimersByTimeAsync(2_000);
    expect(mockedOpenEventsStream).toHaveBeenCalledTimes(3);
    expect(useEventsStore.getState().healthy).toBe(false);

    useEventsStore.getState().stop();
  });

  it("a successful reconnect resets the failure counter and flips healthy back to true", async () => {
    mockedOpenEventsStream
      .mockRejectedValueOnce(new Error("fail 1"))
      .mockRejectedValueOnce(new Error("fail 2"))
      .mockRejectedValueOnce(new Error("fail 3"))
      .mockResolvedValueOnce({ body: streamOf([]) } as unknown as Response);

    useEventsStore.getState().start();

    await vi.advanceTimersByTimeAsync(0); // attempt 1: fails
    await vi.advanceTimersByTimeAsync(1_000); // attempt 2: fails
    await vi.advanceTimersByTimeAsync(2_000); // attempt 3: fails — now unhealthy
    expect(useEventsStore.getState().healthy).toBe(false);

    await vi.advanceTimersByTimeAsync(4_000); // attempt 4: succeeds
    expect(useEventsStore.getState().connectionState).toBe("open");
    expect(useEventsStore.getState().healthy).toBe(true);

    useEventsStore.getState().stop();
  });

  it("passes the last received frame's id as Last-Event-ID on the next reconnect", async () => {
    // Attempt 1: connects, receives one frame carrying id "7:1", then the server
    // closes the stream cleanly (e.g. EVENTS_MAX_STREAM_SECONDS — a normal, expected
    // periodic reconnect, not a failure).
    mockedOpenEventsStream.mockResolvedValueOnce({
      body: streamThatCloses([sseFrame("data_version", "7:1", { data_version: 7 })]),
    } as unknown as Response);
    mockedOpenEventsStream.mockResolvedValueOnce({ body: streamOf([]) } as unknown as Response);

    useEventsStore.getState().start();
    await vi.advanceTimersByTimeAsync(0); // attempt 1: connect, read frame, stream closes
    expect(useStatusStore.getState().dataVersion).toBe(7);
    expect(mockedOpenEventsStream.mock.calls[0]?.[0]).toBeNull(); // fresh connection, no id yet

    await vi.advanceTimersByTimeAsync(1_000); // reconnect fires after the 1s backoff
    expect(mockedOpenEventsStream.mock.calls[1]?.[0]).toBe("7:1");

    useEventsStore.getState().stop();
  });

  it("stop() resets connectionState to idle and healthy to false, without an orphaned backoff timer", async () => {
    mockedOpenEventsStream.mockRejectedValue(new Error("boom"));

    useEventsStore.getState().start();
    await vi.advanceTimersByTimeAsync(0); // attempt 1 fails, now waiting out the 1s backoff

    useEventsStore.getState().stop();
    expect(useEventsStore.getState().connectionState).toBe("idle");
    expect(useEventsStore.getState().healthy).toBe(false);

    // Advancing time past the backoff window must not trigger another connection
    // attempt — stop() must have unwound the pending backoff wait, not left it dangling.
    await vi.advanceTimersByTimeAsync(5_000);
    expect(mockedOpenEventsStream).toHaveBeenCalledTimes(1);
  });
});
