/**
 * pollChain.ts — shared setTimeout-chain polling primitive (FE-ARCH-2).
 *
 * INVARIANT I3/I7: every poll loop in Synapse must be a SINGLE setTimeout chain
 * (never setInterval), driven by an AbortController, and bounded — it stops the
 * instant the caller's `intervalFor` returns null/undefined (terminal state).
 *
 * This module is the ONE place that pattern is implemented. Stores and
 * components must use `createPollChain` (or the `usePollChain` React hook in
 * `src/hooks/usePollChain.ts`) instead of re-implementing the chain locally.
 *
 * Refcounted subscribe(): multiple callers (StrictMode double-invoke, HMR,
 * several mount sites) can all call `subscribe()` and share ONE underlying
 * fetch chain; the chain is aborted only when the last subscriber detaches.
 * A single caller (the common case) gets exactly the same "start on first
 * subscribe, stop on last unsubscribe" behaviour for free.
 */

export interface PollChainOptions<T> {
  /** Perform one poll request. Must respect `signal` for cancellation. */
  fetch: (signal: AbortSignal) => Promise<T>;
  /** Called with each successful, non-aborted result. */
  onResult: (result: T) => void;
  /**
   * Given the latest result, return the delay (ms) before the next tick,
   * or null/undefined to stop the chain (terminal state reached).
   */
  intervalFor: (result: T) => number | null | undefined;
  /** Called on a non-abort error from `fetch`. Side-effect only (e.g. set error state). */
  onError?: (err: unknown) => void;
  /**
   * Given the error, return the delay (ms) before retrying, or null/undefined
   * to stop the chain. Omit to stop the chain on any error (the common case).
   */
  errorIntervalFor?: (err: unknown) => number | null | undefined;
  /** Delay (ms) before the very first tick. Defaults to 0 (poll immediately). */
  initialDelayMs?: number;
  /**
   * Checked immediately before every tick (including the first). Return false
   * to stop the chain WITHOUT issuing another fetch (e.g. a wall-clock deadline).
   */
  shouldContinue?: () => boolean;
  /** Called once when `shouldContinue` first returns false. */
  onGiveUp?: () => void;
}

export interface PollChainController {
  /**
   * Attach a subscriber; starts the chain if it isn't already running.
   * Returns a cleanup function — the chain stops once the last subscriber
   * has called its cleanup (refcounted).
   */
  subscribe: () => () => void;
  /** Force-stop the chain immediately, regardless of refcount. */
  stop: () => void;
  isRunning: () => boolean;
}

export function createPollChain<T>(opts: PollChainOptions<T>): PollChainController {
  let ctrl: AbortController | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let refCount = 0;
  let gaveUp = false;

  function clearTimer(): void {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  }

  function stopInternal(): void {
    ctrl?.abort();
    ctrl = null;
    clearTimer();
    // A chain that reaches a terminal state (natural stop) is fully done —
    // reset the refcount so a later subscribe()/start() creates a fresh run
    // instead of being treated as "still attached" to a dead chain. Any
    // outstanding detach closures remain safe: they clamp refCount at 0.
    refCount = 0;
  }

  async function tick(activeCtrl: AbortController): Promise<void> {
    if (activeCtrl.signal.aborted || ctrl !== activeCtrl) return;

    if (opts.shouldContinue && !opts.shouldContinue()) {
      if (!gaveUp) {
        gaveUp = true;
        opts.onGiveUp?.();
      }
      stopInternal();
      return;
    }

    try {
      const result = await opts.fetch(activeCtrl.signal);
      if (activeCtrl.signal.aborted || ctrl !== activeCtrl) return;
      opts.onResult(result);
      const delay = opts.intervalFor(result);
      if (delay == null) {
        stopInternal();
        return;
      }
      timer = setTimeout(() => void tick(activeCtrl), delay);
    } catch (err: unknown) {
      if (activeCtrl.signal.aborted || ctrl !== activeCtrl) return;
      if (err instanceof Error && err.name === "AbortError") return;
      opts.onError?.(err);
      const delay = opts.errorIntervalFor ? opts.errorIntervalFor(err) : null;
      if (delay == null) {
        stopInternal();
        return;
      }
      timer = setTimeout(() => void tick(activeCtrl), delay);
    }
  }

  function startInternal(): void {
    if (ctrl !== null) return; // already running
    gaveUp = false;
    const activeCtrl = new AbortController();
    ctrl = activeCtrl;
    timer = setTimeout(() => void tick(activeCtrl), opts.initialDelayMs ?? 0);
  }

  return {
    subscribe(): () => void {
      refCount += 1;
      startInternal();
      let detached = false;
      return () => {
        if (detached) return;
        detached = true;
        refCount -= 1;
        if (refCount <= 0) {
          refCount = 0;
          stopInternal();
        }
      };
    },
    stop(): void {
      refCount = 0;
      stopInternal();
    },
    isRunning(): boolean {
      return ctrl !== null;
    },
  };
}
