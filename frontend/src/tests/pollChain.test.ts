/**
 * pollChain.test.ts — unit tests for the shared setTimeout-chain primitive (FE-ARCH-2).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { createPollChain } from "../store/pollChain";

describe("createPollChain", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("fetches immediately by default (initialDelayMs=0) and reschedules per intervalFor", async () => {
    const fetch = vi.fn().mockResolvedValue({ running: true });
    const onResult = vi.fn();
    const chain = createPollChain({
      fetch,
      onResult,
      intervalFor: (r: { running: boolean }) => (r.running ? 100 : null),
    });

    const stop = chain.subscribe();
    await vi.advanceTimersByTimeAsync(0);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(onResult).toHaveBeenCalledWith({ running: true });

    await vi.advanceTimersByTimeAsync(100);
    expect(fetch).toHaveBeenCalledTimes(2);

    stop();
  });

  it("stops the chain when intervalFor returns null (terminal state)", async () => {
    const fetch = vi.fn().mockResolvedValue({ running: false });
    const chain = createPollChain({
      fetch,
      onResult: () => {},
      intervalFor: (r: { running: boolean }) => (r.running ? 100 : null),
    });

    chain.subscribe();
    await vi.advanceTimersByTimeAsync(0);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(chain.isRunning()).toBe(false);

    await vi.advanceTimersByTimeAsync(500);
    expect(fetch).toHaveBeenCalledTimes(1); // never scheduled again
  });

  it("stops on error by default (errorIntervalFor omitted)", async () => {
    const fetch = vi.fn().mockRejectedValue(new Error("boom"));
    const onError = vi.fn();
    const chain = createPollChain({
      fetch,
      onResult: () => {},
      intervalFor: () => 100,
      onError,
    });

    chain.subscribe();
    await vi.advanceTimersByTimeAsync(0);
    expect(onError).toHaveBeenCalledTimes(1);
    expect(chain.isRunning()).toBe(false);

    await vi.advanceTimersByTimeAsync(500);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("continues on error when errorIntervalFor returns a delay", async () => {
    const fetch = vi.fn().mockRejectedValue(new Error("transient"));
    const chain = createPollChain({
      fetch,
      onResult: () => {},
      intervalFor: () => 100,
      errorIntervalFor: () => 50,
    });

    chain.subscribe();
    await vi.advanceTimersByTimeAsync(0);
    expect(fetch).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(50);
    expect(fetch).toHaveBeenCalledTimes(2);
    chain.stop();
  });

  it("never fetches when AbortError is thrown, and does not reschedule", async () => {
    const abortErr = new Error("aborted");
    abortErr.name = "AbortError";
    const fetch = vi.fn().mockRejectedValue(abortErr);
    const onError = vi.fn();
    const chain = createPollChain({
      fetch,
      onResult: () => {},
      intervalFor: () => 100,
      onError,
    });

    chain.subscribe();
    await vi.advanceTimersByTimeAsync(0);
    expect(onError).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(500);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("refcounts subscribe(): the chain keeps running until the LAST subscriber detaches", async () => {
    const fetch = vi.fn().mockResolvedValue({ running: true });
    const chain = createPollChain({
      fetch,
      onResult: () => {},
      intervalFor: () => 100,
    });

    const stopA = chain.subscribe();
    const stopB = chain.subscribe();
    await vi.advanceTimersByTimeAsync(0);
    expect(fetch).toHaveBeenCalledTimes(1); // shared chain — one fetch, not two

    stopA();
    expect(chain.isRunning()).toBe(true); // stopB still attached

    stopB();
    expect(chain.isRunning()).toBe(false);

    await vi.advanceTimersByTimeAsync(500);
    expect(fetch).toHaveBeenCalledTimes(1); // no further ticks after last detach
  });

  it("respects shouldContinue: stops WITHOUT fetching when it returns false", async () => {
    const fetch = vi.fn().mockResolvedValue({ running: true });
    const onGiveUp = vi.fn();
    const chain = createPollChain({
      fetch,
      onResult: () => {},
      intervalFor: () => 100,
      shouldContinue: () => false,
      onGiveUp,
    });

    chain.subscribe();
    await vi.advanceTimersByTimeAsync(0);
    expect(fetch).not.toHaveBeenCalled();
    expect(onGiveUp).toHaveBeenCalledTimes(1);
    expect(chain.isRunning()).toBe(false);
  });

  it("stop() force-stops regardless of refcount", async () => {
    const fetch = vi.fn().mockResolvedValue({ running: true });
    const chain = createPollChain({
      fetch,
      onResult: () => {},
      intervalFor: () => 100,
    });

    chain.subscribe();
    chain.subscribe();
    await vi.advanceTimersByTimeAsync(0);
    chain.stop();
    expect(chain.isRunning()).toBe(false);

    await vi.advanceTimersByTimeAsync(500);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("resets refcount on natural termination so a later subscribe() starts a fresh chain", async () => {
    let running = true;
    const fetch = vi.fn().mockImplementation(() => Promise.resolve({ running }));
    const chain = createPollChain({
      fetch,
      onResult: () => {},
      intervalFor: (r: { running: boolean }) => (r.running ? 100 : null),
    });

    const stopFirst = chain.subscribe();
    await vi.advanceTimersByTimeAsync(0); // fetch #1: running=true → reschedule
    expect(fetch).toHaveBeenCalledTimes(1);

    running = false;
    await vi.advanceTimersByTimeAsync(100); // fetch #2: running=false → terminal stop
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(chain.isRunning()).toBe(false);

    // A stale detach closure from the first subscribe() is still callable —
    // must not throw and must not corrupt a subsequent subscribe().
    stopFirst();

    running = true;
    chain.subscribe();
    await vi.advanceTimersByTimeAsync(0);
    expect(fetch).toHaveBeenCalledTimes(3); // fresh chain actually restarted
    chain.stop();
  });

  it("honours initialDelayMs before the first tick", async () => {
    const fetch = vi.fn().mockResolvedValue({ running: false });
    const chain = createPollChain({
      fetch,
      onResult: () => {},
      intervalFor: () => null,
      initialDelayMs: 3000,
    });

    chain.subscribe();
    await vi.advanceTimersByTimeAsync(2999);
    expect(fetch).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
